#!/usr/bin/env python
import json
import sys
from collections import defaultdict
from time import time

from slacker import Slacker
import requests
import pytz
import os
from datetime import datetime
from dateutil import parser
import logging

# use DEBUG to show API requests and responses
logging.basicConfig(format='%(levelname)s:%(message)s',
                    level=logging.INFO)


# only run on weekdays
if datetime.today().weekday() > 4:
    sys.exit()


# Slack setup #

slack_token = os.environ.get('SLACK_API_TOKEN')
slack = Slacker(slack_token)


def slack_post(channel, message):
    logging.info(f"Posting to Slack channel {channel}: {message}")
    if slack_token:
        slack.chat.post_message(channel, message, as_user=True)
    else:
        logging.info("No slack token!")


# Front integration #

front_users = json.loads(os.environ.get('FRONT_USERS'))
front_token = os.environ.get('FRONT_API_TOKEN')
# FRONT_INBOX_TO_SLACK_CHANNEL maps Front inboxes to Slack channels,
# like 'Inbox Name#channel-name|Inbox Name#channel-name...'
try:
    front_inbox_to_slack_channel = dict(token.split('#')
                                        for token
                                        in os.environ.get('FRONT_INBOX_TO_SLACK_CHANNEL').split('|'))  # noqa
except ValueError:
    # the env var is empty
    front_inbox_to_slack_channel = {}


def front_api(url, params={}):
    return requests.get(url,
                        params,
                        headers={'Authorization': f'Bearer {front_token}'}).json()  # noqa


def front_conversation_age(conversation):
    """
    return number of days since last message in given Front conversation
    """
    return int((time() - conversation['last_message']['created_at']) / 60 / 60 / 24)  # noqa


# fetch each Front inbox
inboxes = front_api('https://api2.frontapp.com/inboxes')

for i, inbox in enumerate(inboxes['_results']):

    # skip inboxes not defined in FRONT_INBOX_TO_SLACK_CHANNEL
    if inbox['name'] not in front_inbox_to_slack_channel:
        continue
    slack_channel = '#' + front_inbox_to_slack_channel[inbox['name']]

    # fetch assigned and unassigned message counts
    conversations_url = inbox['_links']['related']['conversations']
    unassigned = front_api(conversations_url, {'q[statuses][]': 'unassigned'})
    old_unassigned = [c for c in unassigned['_results']
                      if front_conversation_age(c) >= 1]
    unassigned_count = len(old_unassigned)
    assigned = front_api(conversations_url, {'q[statuses][]': 'assigned'})
    assigned_counts = defaultdict(lambda: {'count': 0, 'max_age': 0})
    for c in assigned['_results']:
        age = front_conversation_age(c)
        if age < 1:
            continue
        username = c['assignee']['username']
        assigned_counts[username]['count'] += 1
        assigned_counts[username]['max_age'] = max(assigned_counts[username]['max_age'], age)  # noqa

    # post message to Slack
    if not unassigned_count and not assigned_counts:
        continue
    if unassigned_count:
        slack_post(slack_channel,
                   f"{unassigned_count} Front messages unassigned for over a day!")  # noqa
    for k, v in assigned_counts.items():
        front_user = front_users.get(k)
        slack_name = "@"+front_user['user'] if front_user else k
        emoji = f":{front_user['emoji']}:" if front_user and front_user['emoji'] else ""  # noqa
        message = f"{v['count']} message{'s' if v['count'] > 1 else ''} assigned to {slack_name} {emoji} -- oldest is {v['max_age']} day{'s' if v['max_age'] > 1 else ''} old"  # noqa
        slack_post(slack_channel, message)

# Github integration #

# NUDGE_PULLS_URL_CHANNEL is a list of urls and channels
# like 'url1#channel1|url2#channel2...'
# each URL is an endpoint like
# https://api.github.com/repos/harvard-lil/perma/pulls
for target in os.environ.get('NUDGE_PULLS_URL_CHANNEL').split('|'):
    url, channel = target.split('#')
    channel = '#' + channel

    # Pass the custom accept header to requests,
    # "to access the new draft parameter during the preview period"
    pull_reqs = requests.get(url,
                             headers={'accept': 'application/vnd.github.shadow-cat-preview+json'}).json()  # noqa

    now = datetime.now(pytz.utc)
    nudged = False
    for pull_req in pull_reqs:
        # calculate emoji based on age of PR
        requested_at = parser.parse(pull_req['created_at'])
        time_diff = now - requested_at
        hours_since_pull_req = int(time_diff.total_seconds() / 60 / 60)
        if hours_since_pull_req < 36:
            emoji = ':wink:'
        elif hours_since_pull_req < 48:
            emoji = ':grimacing:'
        elif hours_since_pull_req < 60:
            emoji = ':expressionless:'
        elif hours_since_pull_req < 72:
            emoji = ':cry:'
        else:
            emoji = ':triumph:'

        # Fetch any reviews requesting changes
        # https://developer.github.com/v3/pulls/reviews/
        reviews_url = pull_req['url'] + '/reviews'
        reviews = requests.get(pull_req['url'] + '/reviews').json()

        ignore_pr = pull_req['draft'] or any(label['name'] == 'no-nudge'
                                             for label in pull_req['labels'])

        if not ignore_pr:
            nudged = True
            user = pull_req['head']['user']['login']

            # Get combined status for this pull req.
            # To do this we convert the `statuses_url` GitHub gave us from
            # something like
            #       https://api.github.com/repos/harvard-lil/perma/statuses/de6a92521c5988e735435a41103514f7961d377c  # noqa
            # to
            #       https://api.github.com/repos/harvard-lil/perma/commits/de6a92521c5988e735435a41103514f7961d377c/status  # noqa
            # so GitHub will give us a single success or failure `state`
            # in the response.
            status_summary_url = pull_req['statuses_url'].replace('/statuses/', '/commits/') + '/status'  # noqa
            status_summary = requests.get(status_summary_url).json()

            # If tests have failed, send a message to the channel --
            # we can't send to just the user, since github username and slack
            # username don't necessarily match.
            if status_summary['state'] == 'failure':
                message = "Uh oh -- tests have failed on %s's %s %s" % (
                    pull_req['head']['user']['login'],
                    pull_req['html_url'], emoji
                )
            elif any(review['state'] == 'CHANGES_REQUESTED'
                     for review in reviews):
                message = "Changes requested on %s's %s" % (
                    pull_req['head']['user']['login'], pull_req['html_url']
                )
            else:
                message = "Don't keep %s waiting %s %s" % (
                    pull_req['head']['user']['login'], emoji,
                    pull_req['html_url']
                )

            # fetch any requested reviewers
            reviewers = pull_req['requested_reviewers']
            if reviewers:
                s = '' if len(reviewers) == 1 else 's'
                handles = ', '.join([r['login'] for r in reviewers])
                message += f'Pending reviewer{s}: {handles}'

            slack_post(channel, message)
        else:
            logging.info(f"Ignoring {pull_req['html_url']}")

    if not nudged:
        logging.info("No nudging necessary for {}".format(url))
