#!/usr/bin/env python

# a Slack bot for nudging about old pull requests
#
# this needs to be run via a cron job or via Heroku
# scheduler from time to time

from slacker import Slacker
import requests
import json
import pytz
import os
from datetime import datetime
from dateutil import parser
import logging

logging.basicConfig(format='%(levelname)s:%(message)s',
                    level=logging.DEBUG)

# only run on weekdays
if datetime.today().weekday() in range(5):
    slack = Slacker(os.environ.get('SLACK_API_TOKEN'))
    targets = os.environ.get('NUDGE_PULLS_URL_CHANNEL')
    # this is awkward, and requires a well-formed env var: 'url1#channel1|url2#channel2' etc.,
    # where NUDGE_PULLS_URL_SPLIT_ON is '|'
    for (url, channel) in [(target[0], '#{}'.format(target[1]))
                           for target in map(lambda x: x.split('#'),
                                             targets.split(os.environ.get('NUDGE_PULLS_URL_SPLIT_ON')))]:
        r = requests.get(url)
        jsoned_response = json.loads(r.text)

        now = datetime.now(pytz.utc)

        nudged = False

        for pull_req in jsoned_response:
            requested_at = parser.parse(pull_req['created_at'])
            time_diff = now - requested_at
            hours_since_pull_req = int(time_diff.total_seconds() / 60 / 60)

            emoji = False
            if hours_since_pull_req in range(12, 36):
                emoji = ':wink:'
            elif hours_since_pull_req in range(36, 48):
                emoji = ':grimacing:'
            elif hours_since_pull_req in range(48, 60):
                emoji = ':expressionless:'
            elif hours_since_pull_req in range(60, 72):
                emoji = ':cry:'
            elif hours_since_pull_req >= 72:
                emoji = ':triumph:'

            # Ignore pull requests that have any reviews requesting changes
            # Pass the custom accept header to requests, because this API is preview mode
            # https://developer.github.com/v3/pulls/reviews/
            reviews_url = pull_req['url'] + '/reviews'
            reviews = json.loads(requests.get(reviews_url,
                                              headers={'accept': 'application/vnd.github.black-cat-preview+json'}).text)

            if emoji and not any(review['state'] == 'CHANGES_REQUESTED' for review in reviews):
                nudged = True
                user = pull_req['head']['user']['login']

                # Get combined status for this pull req.
                # To do this we convert the `statuses_url` GitHub gave us from something like
                #       https://api.github.com/repos/harvard-lil/perma/statuses/de6a92521c5988e735435a41103514f7961d377c
                # to
                #       https://api.github.com/repos/harvard-lil/perma/commits/de6a92521c5988e735435a41103514f7961d377c/status
                # so GitHub will give us a single success or failure `state` in the response.
                status_summary_url = pull_req['statuses_url'].replace('/statuses/', '/commits/') + '/status'
                status_summary = json.loads(requests.get(status_summary_url).text)

                # If tests have failed, send a message to the channel -- we can't send to just the user,
                # since github username and slack username don't necessarily match.
                if status_summary['state'] == 'failure':
                    message = "Uh oh -- tests have failed on %s's %s %s" % (pull_req['head']['user']['login'],
                                                                            pull_req['html_url'], emoji)
                else:
                    message = "Don't keep %s waiting %s %s" % (pull_req['head']['user']['login'], emoji,
                                                               pull_req['html_url'])

                slack.chat.post_message(channel, message, as_user=True)
                logging.info("Nudged about %s" % (pull_req['html_url'],))

        if not nudged:
            logging.info("No nudging necessary for {}".format(url))
