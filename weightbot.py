#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Telegram Bot to collect weight measurements and their timestamp into a
CSV file for some very simple statistical analysis and goal follow-up."""

import configparser
import csv
import logging
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pendulum
import telegram
from pandas.plotting import register_matplotlib_converters
from telegram.ext import (
    BaseFilter,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    Updater,
)

register_matplotlib_converters()


BASEDIR = Path(__file__).parent

CONFIG = configparser.ConfigParser(inline_comment_prefixes="#")
CONFIG.read((BASEDIR / "config", BASEDIR / "config.local"))
CONFIG = CONFIG["weightbot"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)


class WeightFilter(BaseFilter):
    """Check if the given weight is acceptable or not."""

    @staticmethod
    def filter(message):
        """Check if the given weight is acceptable or not."""
        try:
            weight = float(message.text)
            return 50 < weight < 150
        except ValueError:
            return False


def bot_start(update: telegram.Update, context: CallbackContext):
    """Send a welcome message."""
    update.message.reply_text(
        "Hi! Just type in your current weight and I'll store it for you!"
    )


def bot_error(update: telegram.Update, context: CallbackContext):
    """Log errors caused by updates."""
    LOGGER.warning(f"Update {update} caused error {context.error}")
    if update:
        update.message.reply_text("[some error occurred; check the log]")


def bot_weight(update: telegram.Update, context: CallbackContext):
    """Store the given weight (if found acceptable)."""
    context.bot.send_chat_action(
        chat_id=update.message.chat_id, action=telegram.ChatAction.TYPING
    )
    weight = update.message.text
    store_weight(weight)
    update.message.reply_text(f"{weight}kg successfully stored!")
    bot_stats(update, context)


def bot_stats(update: telegram.Update, context: CallbackContext):
    """Generate more elaborate progress statistics."""
    context.bot.send_chat_action(
        chat_id=update.message.chat_id, action=telegram.ChatAction.TYPING
    )

    data = pd.read_csv(
        CONFIG["csvfile"], parse_dates=["timestamp"], index_col="timestamp"
    )
    data.index = pd.to_datetime(data.index, utc=True).tz_convert(
        "Europe/Brussels"
    )

    weekweight_mean_weight = data.last("7d").weight.mean()

    weight_min_weight = data.loc[data.weight.idxmin()].weight
    weight_min_timestamp = pendulum.instance(
        data.weight.idxmin()
    ).diff_for_humans()

    weight_max_weight = data.loc[data.weight.idxmax()].weight
    weight_max_timestamp = pendulum.instance(
        data.weight.idxmax()
    ).diff_for_humans()

    means = data.resample("W", kind="period").mean()
    weight_orig = means.weight[0]
    weight_now = means.weight[-1]
    weight_loss = weight_orig - weight_now
    weight_loss_period = (
        data.index.max() - data.index.min()
    ) / np.timedelta64(1, "D")
    weight_goal = weight_orig + (
        12 * float(CONFIG["goal"]) / 365 * weight_loss_period
    )

    fig, ax = plt.subplots()
    ax.plot(data, "k.")
    means.plot.line(ax=ax, style="g" if weight_now <= weight_goal else "r")
    ax.plot(
        [means.index[0].start_time, means.index[-1].start_time],
        [weight_orig, weight_goal],
        "--",
        color="orange",
    )
    ax.set_ylim(
        [min(weight_goal, weight_min_weight) - 1, weight_max_weight + 1]
    )
    ax.yaxis.set_ticks_position("both")
    ax.get_legend().remove()
    ax.tick_params(labeltop=False, labelright=True)
    plt.xlabel("")
    plt.ylabel("kg")
    fig.autofmt_xdate()

    update.message.reply_text(
        f"Your weight mean the past week is {weekweight_mean_weight:.1f}kg. "
        f"The minimum over the complete period was {weight_min_weight:.1f}kg "
        f"({weight_min_timestamp}) and maximum was {weight_max_weight:.1f}kg "
        f"({weight_max_timestamp})."
    )
    context.bot.send_chat_action(
        chat_id=update.message.chat_id, action=telegram.ChatAction.TYPING
    )

    with tempfile.NamedTemporaryFile(suffix=".png") as figfile:
        fig.savefig(figfile.name, bbox_inches="tight")
        update.message.reply_photo(figfile)
    gainedlost = "lost" if weight_loss >= 0 else "gained"
    update.message.reply_text(
        f"You have {gainedlost} {weight_loss:.1f}kg "
        f"in {weight_loss_period:.0f} days"
    )


def store_weight(weight):
    """Write the given weight to the CSV file with the current timestamp."""
    with open(CONFIG["csvfile"], mode="a", newline="") as csvfile:
        weightwriter = csv.writer(csvfile)
        weightwriter.writerow([pendulum.now(), weight])


def main():
    """Run bot."""
    csvfile_path = Path(CONFIG["csvfile"])
    if not csvfile_path.is_file() or csvfile_path.stat().st_size == 0:
        with csvfile_path.open(mode="w", newline="") as csvfile:
            weightwriter = csv.writer(csvfile)
            weightwriter.writerow(["timestamp", "weight"])

    updater = Updater(CONFIG["token"], use_context=True)

    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler("start", bot_start))
    dispatcher.add_handler(CommandHandler("stats", bot_stats))
    dispatcher.add_handler(MessageHandler(WeightFilter(), bot_weight))
    dispatcher.add_error_handler(bot_error)

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
