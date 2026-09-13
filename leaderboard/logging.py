import logging
import re


class RedactTokens(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(
            r"(/invitations/accept/|/accounts/reset/)[^\s?\"']+",
            r"\1[redacted]/",
            message,
        )
        record.args = ()
        return True
