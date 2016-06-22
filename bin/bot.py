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

slack = Slacker(os.environ.get('SLACK_API_TOKEN'))
r = requests.get(os.environ.get('NUDGE_PULLS_URL'))
jsoned_response = json.loads(r.text)

now = datetime.now(pytz.utc)

for pull_req in jsoned_response:
    requested_at = parser.parse(pull_req['created_at'])
    time_diff = now - requested_at
    hours_since_pull_req = int(time_diff.total_seconds() /60/60)

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

    if emoji:
        slack.chat.post_message(os.environ.get('NUDGE_CHANNEL'), "Don't keep %s waiting %s %s" % (pull_req['head']['user']['login'], emoji, pull_req['html_url']), as_user=True)
        logging.info("Nudged about %s" % (pull_req['html_url'],))
        break
else:
    logging.info("No nudging necessary")
