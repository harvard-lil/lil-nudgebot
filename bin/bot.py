#!/usr/bin/env python
import json
import sys

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


# Github integration #

# GITHUB_USERS is a dict of GitHub users and matching Slack IDs
github_users = json.loads(os.environ.get('GITHUB_USERS'))

# NUDGE_PULLS_URL_CHANNEL is a dict of URLs and channels
# each URL is an endpoint like
# https://api.github.com/repos/harvard-lil/perma/pulls
for url, channel in json.loads(os.environ.get('NUDGE_PULLS_URL_CHANNEL')).items():  # noqa
    # Pass the custom accept header to requests,
    # "to access the new draft parameter during the preview period"
    headers = {'accept': 'application/vnd.github.shadow-cat-preview+json'}
    pull_reqs = requests.get(url, headers=headers).json()

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
                handles = ', '.join([github_users.get(r['login'], r['login']) for r in reviewers])  # noqa
                message += f'\nPending reviewer{s}: {handles}'

            slack_post(f'#{channel}', message)
        else:
            logging.info(f"Ignoring {pull_req['html_url']}")

    if not nudged:
        logging.info("No nudging necessary for {}".format(url))
