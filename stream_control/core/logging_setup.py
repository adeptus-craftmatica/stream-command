from __future__ import annotations

import logging


def configure_app_logging() -> None:
    noisy_loggers = [
        "obsws_python",
        "obsws_python.baseclient",
        "obsws_python.reqs",
        "websocket",
        "slobsapi",
        "slobsapi._SlobsWebSocket",
        "slobsapi.SlobsConnection",
    ]

    for logger_name in noisy_loggers:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
