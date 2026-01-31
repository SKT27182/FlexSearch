import logging
from logging import Logger
import datetime
import os
from copy import deepcopy

from typing import Optional, List, Literal, Callable, Dict

import time
import json

################################################

LOG_PATH = "/home/skt27182/logs"

# Color mappings
COLOUR_MAPPING = {
    "BLACK": "\033[30m",
    "RED": "\033[31m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "BLUE": "\033[34m",
    "MAGENTA": "\033[35m",
    "CYAN": "\033[36m",
    "WHITE": "\033[37m",
    "END": "\033[0m",
    "HEADER": "\033[95m",
    "OKBLUE": "\033[94m",
    "OKGREEN": "\033[92m",
    "WARNING": "\033[93m",
    "FAIL": "\033[91m",
    "BOLD": "\033[1m",
    "UNDERLINE": "\033[4m",
}


class ColumnNotFound(Exception):
    def __init__(self, columns):
        self.message = f"Avialable columns are: {columns}"
        super().__init__(self.message)


############################################# Logger #######################################


def create_suppression_filter(suppressed_loggers):
    def filter_func(record):
        return record.name not in suppressed_loggers

    return filter_func


def add_logging_level(
    level_name: str, level_num: int, method_name: Optional[str] = None
):
    if not method_name:
        method_name = level_name.lower()

    def log_for_level(self, message, *args, **kwargs):
        if self.isEnabledFor(level_num):
            self._log(level_num, message, args, **kwargs)

    def log_to_root(message, *args, **kwargs):
        logging.log(level_num, message, *args, **kwargs)

    logging.addLevelName(level_num, level_name.upper())
    setattr(logging, level_name.upper(), level_num)
    setattr(logging.getLoggerClass(), method_name, log_for_level)
    setattr(logging, method_name, log_to_root)


class CustomFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": COLOUR_MAPPING["CYAN"],
        "INFO": COLOUR_MAPPING["GREEN"],
        "VERBOSE": COLOUR_MAPPING["WHITE"],
        "WARNING": COLOUR_MAPPING["YELLOW"],
        "ERROR": COLOUR_MAPPING["RED"] + COLOUR_MAPPING["BOLD"],
        "CRITICAL": COLOUR_MAPPING["RED"]
        + COLOUR_MAPPING["BOLD"]
        + COLOUR_MAPPING["UNDERLINE"],
    }

    def format(self, record):
        formatted_record = deepcopy(record)

        level_name = formatted_record.levelname
        color = self.COLORS.get(level_name, "")

        # Adjust name based on whether it's in the main module or not
        formatted_record.name = (
            formatted_record.name
            if formatted_record.funcName == "<module>"
            else f"{formatted_record.name}.{formatted_record.funcName}"
        )

        # Create the log message without color first
        custom_format = "%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s"
        formatter = logging.Formatter(custom_format, datefmt="%Y-%m-%d %H:%M:%S")
        log_message = formatter.format(formatted_record)

        # Then apply color to the entire message
        colored_message = f"{color}{log_message}{COLOUR_MAPPING['END']}"

        return colored_message


class JSONFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)

    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "name": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        if datefmt:
            s = time.strftime(datefmt, ct)
        else:
            t = time.localtime(record.created)
            s = time.strftime("%Y-%m-%d %H:%M:%S", t)
        return s


def create_logger(
    name: str,
    level: Literal[
        "notset",
        "debug",
        "info",
        "verbose",
        "warning",
        "error",
        "critical",
    ] = "info",
    log_file: Optional[str] = None,
    consolidate_file_loggers: bool = True,
    use_json: bool = True,
    filters: Optional[List[Callable[[logging.LogRecord], bool]]] = None,
    custom_levels: Optional[Dict[str, int]] = None,
    suppress_loggers: Optional[List[str]] = None,
) -> Logger:
    """
    Create a logger with the specified name and level, including color formatting for console
    and JSON formatting for file output, with optional custom filters.

    Args:
        name: Name of the logger.
        level: Logging level. Defaults to "info".
        log_file: Name of the log file. Defaults to None.
        consolidate_file_loggers: Whether to consolidate file loggers. Defaults to True.
        use_json: Whether to use JSON formatting for file logging. Defaults to True.
        filters: List of custom filter functions to apply to the logger. Defaults to None.
        custom_levels: Dictionary of custom log level names and their corresponding integer values.


    allowed_colours = ["black", "red", "green", "yellow", "blue", "cyan", "white",
                    "bold_black", "bold_red", "bold_green", "bold_yellow", "bold_blue",
                     "bold_cyan", "bold_white",
                    ]



    Returns:
        Configured logger object.
    """
    verbose_level: int = logging.INFO + 3

    level_to_int_map: Dict[str, int] = {
        "notset": logging.NOTSET,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "verbose": verbose_level,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }

    ## Adding custom log level verbose
    add_logging_level("verbose", verbose_level)
    level_to_int_map["verbose"] = verbose_level

    # Add custom levels
    if custom_levels:
        for level_name, level_value in custom_levels.items():
            add_logging_level(level_name, level_value)
            level_to_int_map[level_name.lower()] = level_value
            CustomFormatter.COLORS[level_name.upper()] = COLOUR_MAPPING[
                "BLUE"
            ]  # Default color for custom levels

    logger: Logger = logging.getLogger(name)
    level_int: int = (
        level_to_int_map[level.lower()] if isinstance(level, str) else level
    )
    logger.setLevel(level_int)

    # Add suppression filter if suppress_loggers is provided
    if suppress_loggers:
        suppression_filter = create_suppression_filter(suppress_loggers)
        logger.addFilter(suppression_filter)

    custom_formatter = CustomFormatter(
        "%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    json_formatter = JSONFormatter()

    # Remove existing handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    # Console handler with color formatting
    console_handler: logging.StreamHandler = logging.StreamHandler()
    console_handler.setLevel(level_int)
    console_handler.setFormatter(custom_formatter)
    logger.addHandler(console_handler)

    # File logging setup
    if log_file:
        today: str = datetime.datetime.now().strftime("%Y_%m_%d")
        curr_time: str = datetime.datetime.now().strftime("%H_%M_%S")
        log_dir: str = os.path.join(LOG_PATH, today, log_file)
        os.makedirs(log_dir, exist_ok=True)
        log_file_path: str = os.path.join(
            log_dir, f"{curr_time}.json" if use_json else f"{curr_time}.log"
        )
        file_handler: logging.FileHandler = logging.FileHandler(log_file_path)
        file_handler.setLevel(level_int)
        file_handler.setFormatter(
            json_formatter
            if use_json
            else logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        if consolidate_file_loggers:
            logging.getLogger().addHandler(file_handler)
        else:
            logger.addHandler(file_handler)

    # Add custom filters
    if filters:
        for filter_func in filters:
            logger.addFilter(filter_func)

    return logger
