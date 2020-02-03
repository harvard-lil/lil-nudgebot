#!/usr/bin/env python

from collections import Counter
from slacker import Slacker
import requests
import pytz
import os
from datetime import datetime
from dateutil import parser
import logging

logging.basicConfig(format='%(levelname)s:%(message)s',
                    level=logging.INFO)  # use DEBUG to show API requests and responses

# only run on weekdays
if datetime.today().weekday() in range(5):

    slack_token = os.environ.get('SLACK_API_TOKEN')
    slack = Slacker(slack_token)

    ### Front integration ###

    front_token = os.environ.get('FRONT_API_TOKEN')
    # FRONT_INBOX_TO_SLACK_CHANNEL maps Front inboxes to Slack channels, like 'Inbox Name#channel-name|Inbox Name#channel-name...'
    front_inbox_to_slack_channel = dict(token.split('#') for token in os.environ.get('FRONT_INBOX_TO_SLACK_CHANNEL').split('|'))

    def front_api(url, params={}):
        return requests.get(url, params, headers={'Authorization': f'Bearer {front_token}'}).json()

    # fetch each Front inbox
    inboxes = front_api('https://api2.frontapp.com/inboxes')
    for inbox in inboxes['_results']:

        # skip inboxes not defined in FRONT_INBOX_TO_SLACK_CHANNEL
        if inbox['name'] not in front_inbox_to_slack_channel:
            continue
        slack_channel = '#' + front_inbox_to_slack_channel[inbox['name']]

        # fetch assigned and unassigned message counts
        conversations_url = inbox['_links']['related']['conversations']
        unassigned = front_api(conversations_url, {'q[statuses][]': 'unassigned'})
        unassigned_count = len(unassigned['_results'])
        assigned = front_api(conversations_url, {'q[statuses][]': 'assigned'})
        assigned_counts = Counter((a['assignee']['first_name'] or a['assignee']['username']) for a in assigned['_results'])

        # post message to Slack
        message = f"Front status: {unassigned_count} messages unassigned"
        for k, v in assigned_counts.items():
            message += f"\n* {v} message{'s' if v > 1 else ''} assigned to {k}"
        logging.info(f"Posting to Slack channel {slack_channel}: {message}")
        if slack_token:
            slack.chat.post_message(slack_channel, message, as_user=True)
        else:
            logging.info("No slack token!")

    ### Github integration ###

    # NUDGE_PULLS_URL_CHANNEL is a list of urls and channels like 'url1#channel1|url2#channel2...'
    # each URL is an endpoint like https://api.github.com/repos/harvard-lil/perma/pulls
    for target in os.environ.get('NUDGE_PULLS_URL_CHANNEL').split('|'):
        url, channel = target.split('#')
        channel = '#' + channel

        # Pass the custom accept header to requests, "to access the new draft parameter during the preview period"
        pull_reqs = requests.get(url, headers={'accept': 'application/vnd.github.shadow-cat-preview+json'}).json()

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
            reviews = requests.get(pull_req['url'] + '/reviews').json()  # https://developer.github.com/v3/pulls/reviews/

            ignore_pr = pull_req['draft'] or any(label['name'] == 'no-nudge' for label in pull_req['labels'])

            if not ignore_pr:
                nudged = True
                user = pull_req['head']['user']['login']

                # Get combined status for this pull req.
                # To do this we convert the `statuses_url` GitHub gave us from something like
                #       https://api.github.com/repos/harvard-lil/perma/statuses/de6a92521c5988e735435a41103514f7961d377c
                # to
                #       https://api.github.com/repos/harvard-lil/perma/commits/de6a92521c5988e735435a41103514f7961d377c/status
                # so GitHub will give us a single success or failure `state` in the response.
                status_summary_url = pull_req['statuses_url'].replace('/statuses/', '/commits/') + '/status'
                status_summary = requests.get(status_summary_url).json()

                # If tests have failed, send a message to the channel -- we can't send to just the user,
                # since github username and slack username don't necessarily match.
                if status_summary['state'] == 'failure':
                    message = "Uh oh -- tests have failed on %s's %s %s" % (pull_req['head']['user']['login'],
                                                                            pull_req['html_url'], emoji)
                elif any(review['state'] == 'CHANGES_REQUESTED' for review in reviews):
                    message = "Changes requested on %s's %s" % (pull_req['head']['user']['login'], pull_req['html_url'])
                else:
                    message = "Don't keep %s waiting %s %s" % (pull_req['head']['user']['login'], emoji,
                                                               pull_req['html_url'])

                logging.info(f"Posting to Slack channel {channel}: {message}")
                if slack_token:
                    slack.chat.post_message(channel, message, as_user=True)
                else:
                    logging.info("No slack token!")
            else:
                logging.info(f"Ignoring {pull_req['html_url']}")

        if not nudged:
            logging.info("No nudging necessary for {}".format(url))
