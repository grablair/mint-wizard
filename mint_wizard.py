import re
import json
import sys
import argparse
import time
import logging
import logging.config

from datetime import datetime, timedelta
from pytimeparse2 import parse as timeparse

from splitwise_helper import SplitwiseHelper
from mint_helper import MintHelper
from db import Db

if __name__ == "__main__":
	logging.config.fileConfig("./logging.ini", disable_existing_loggers=False)

logger = logging.getLogger(__name__)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

def list_recurring_txns(args):
	logger.info("Listing recurring transactions")
	[print(txn) for txn in args.db.get_all_recurring_transactions()]

def add_recurring_txn(args):
	logger.info("Adding recurring transaction")
	args.db.create_recurring_transaction(args.description, args.amount, args.category, args.frequency, args.first_occurrence, args.stop_after)

def remove_recurring_txn(args):
	logger.info("Removing recurring transaction")
	args.db.remove_recurring_transaction(args.id)

def run_auto_processor(args):
	logger.info("Starting run of the Mint Auto-Processor")

	if args.mint_custom_user_identifier and not re.match(r'^[A-Z]+$', args.mint_custom_user_identifier):
		print("--mint-custom-user-identifier must be solely positive letters from A-Z. It was: {}".format(args.mint_custom_user_identifier))
		sys.exit()

	creds = json.load(open(args.credentials_path))
	config = json.load(open(args.config))

	mint = MintHelper(creds, args.db, args.headless)

	if args.splitwise:
		splitwise = SplitwiseHelper(creds, mint, args.shorthand_json_path, args.splitwise_user_id_to_name_json, args.mint_custom_user_identifier)

		# Process Splitwise expenses and add transactions to Mint
		splitwise.process_splitwise_expenses(args.splitwise_days_to_look_back)
	else:
		logger.info("Skipping Splitwise processing, as instructed")

	# Recategorize transactions in Mint
	# TODO: Move to database with multiple patterns (name, price, etc)
	if args.recategorize_txns and "patterns_to_recategorize" in config:
		mint.recategorize_target_transactions(config["patterns_to_recategorize"])

	if args.recurring_txns:
		# Add any recurring transactions to Mint
		mint.process_recurring_transactions()

	mint.close()
	logger.info("Mint auto-processing complete!")

if __name__ == "__main__":

	parser = argparse.ArgumentParser()
	parser.add_argument("-db", "--db-path", help="The path to the sqlite database file", default="./mint-wizard.db", type=(lambda db_path: Db(db_path)), dest="db")

	subparsers = parser.add_subparsers(required=True)

	auto_process_parser = subparsers.add_parser("auto-process", help="Run the auto-processor")
	auto_process_parser.add_argument("-creds", "--credentials-path", help="The path to the file containing your credentials", required=True)
	auto_process_parser.add_argument("-short", "--shorthand-json-path", help="The path to the file containing the mapping of shorthand identifiers to mint categories", default="./shorthands.json")
	auto_process_parser.add_argument("-names", "--splitwise-user-id-to-name-json", help="The path of the JSON file used to override names fetched from Splitwise")
	auto_process_parser.add_argument("-mintid", "--mint-custom-user-identifier", help="Turns on user-specific Splitwise flags. See README")
	auto_process_parser.add_argument("-config", help="Path to config file with recurring transactions and recategorizations", default="./config.json")
	auto_process_parser.add_argument("-days", "--splitwise-days-to-look-back", help="The number of days to look back when determining expenses to process (script looks at updated dates, not dates of the expenses)", type=int, default=7)
	auto_process_parser.add_argument("--recurring-txns", help="Process recurring transactions", action=argparse.BooleanOptionalAction, default=True)
	auto_process_parser.add_argument("--recategorize-txns", help="Perform transaction recategorization", action=argparse.BooleanOptionalAction, default=True)
	auto_process_parser.add_argument("--splitwise", help="Process splitwise transactions", action=argparse.BooleanOptionalAction, default=True)
	auto_process_parser.add_argument("--headless", help="Run the Selenium driver in headless mode", action=argparse.BooleanOptionalAction, default=True)
	auto_process_parser.set_defaults(func=run_auto_processor)

	recurring_transactions_subparser = subparsers.add_parser("recurring-txns", help="Configure recurring Mint transactions")
	recurring_transactions_subparsers = recurring_transactions_subparser.add_subparsers(required=True)

	recurring_transactions_subparsers.add_parser("list", help="List the configured recurring transactions").set_defaults(func=list_recurring_txns)
	
	add_recurring_txn_parser = recurring_transactions_subparsers.add_parser("add", help="Add a new recurring transaction")
	add_recurring_txn_parser.add_argument("-d", "--description", help="Description of the transaction", required=True)
	add_recurring_txn_parser.add_argument("-a", "--amount", help="Amount of the transaction. Negative numbers are charges, positive numbers are credits", required=True)
	add_recurring_txn_parser.add_argument("-c", "--category", help="Mint category for the transaction", required=True)
	add_recurring_txn_parser.add_argument("-f", "--frequency", help="Frequency of recurrence. Ex: 7d", required=True, type=(lambda frequency: timeparse(frequency, as_timedelta=True)))
	add_recurring_txn_parser.add_argument("-fd", "--first-occurrence", help="String representation of the first occurrence, in the following format: YYYY-MM-DD HH:MM", required=True, type=lambda s: datetime.strptime(s, '%Y-%m-%d %H:%M'))
	add_recurring_txn_parser.add_argument("-sd", "--stop-after", help="Optional string representation of the ending datetime for recurrence, in the following format: YYYY-MM-DD HH:MM", type=lambda s: datetime.strptime(s, '%Y-%m-%d %H:%M'))
	add_recurring_txn_parser.set_defaults(func=add_recurring_txn)

	remove_recurring_txn_parser = recurring_transactions_subparsers.add_parser("remove", help="Remove a recurring transaction")
	remove_recurring_txn_parser.add_argument("-id", required=True)
	remove_recurring_txn_parser.set_defaults(func=remove_recurring_txn)

	args = parser.parse_args()
	args.func(args)
