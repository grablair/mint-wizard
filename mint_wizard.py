import re
import json
import sys
import argparse
import time
import logging
import logging.config
import os

from datetime import datetime, timedelta
from pytimeparse2 import parse as timeparse

from splitwise_helper import SplitwiseHelper
from monarch_money_helper import MonarchMoneyHelper
from db import Db
import util

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
	[logger.info(txn) for txn in args.db.get_all_recurring_transactions()]

def add_recurring_txn(args):
	logger.info("Adding recurring transaction")

	description = args.description
	shorthands = json.load(open(args.shorthand_json_path))

	# check for category validity
	if args.category in shorthands.values():
		category = args.category
	elif args.category in shorthands.keys():
		category = shorthands[args.category]
	else:
		logger.error(f"ERROR: Given category {args.category} not a valid shorthand or category.")
		sys.exit(1)

	if args.move_from_category:
		# check for category validity
		if args.move_from_category in shorthands.values():
			move_from_category = args.move_from_category
		elif args.move_from_category in shorthands.keys():
			move_from_category = shorthands[args.move_from_category]
		else:
			logger.error(f"ERROR: Given MOVE FROM category {args.move_from_category} not a valid shorthand or Mint category.")
			sys.exit(1)

		logger.info(f"Creating a MOVE from \"{move_from_category}\" to \"{category}\"")

		description = f"{description} (move from \"{move_from_category}\")"
		args.db.create_recurring_transaction(f"{args.description} (move to \"{category}\")", str(-float(args.amount)), move_from_category, args.recurring_event)

	args.db.create_recurring_transaction(description, args.amount, category, args.recurring_event)

def remove_recurring_txn(args):
	logger.info("Removing recurring transaction")
	args.db.remove_recurring_transaction(args.id)

def run_auto_processor(args):
	logger.info("Starting run of the Budgeting Auto-Processor")

	creds = json.load(open(args.credentials_path))
	config = json.load(open(args.config))

	mm = MonarchMoneyHelper(creds, args.db, args.mm_session_pickle_file)

	if args.splitwise:
		splitwise = SplitwiseHelper(creds, mm, args.shorthand_json_path, args.splitwise_user_id_to_name_json, args.custom_user_identifier, args.db)

		# Process Splitwise expenses and add transactions to Monarch Money
		splitwise.process_splitwise_expenses(args.splitwise_days_to_look_back)

		if 'payment_import_rules' in config:
			splitwise.import_payments_from_budgeting_app(config['payment_import_rules'])

		if 'loans' in config:
			splitwise.handle_personal_loans(config['loans'])
	else:
		logger.info("Skipping Splitwise processing, as instructed")

	if args.recategorize_txns and "patterns_to_recategorize" in config:
		mm.recategorize_target_transactions(config["patterns_to_recategorize"])

	if args.recurring_txns:
		# Add any recurring transactions to Monarch Money
		mm.process_recurring_transactions()

	if "account_balance_export_webhook" in config :
		# Export account balances to the given webhook
		mm.export_account_balances(config["account_balance_export_webhook"])

	if "auto_splits" in config:
		# Process auto-splits
		mm.handle_auto_splits(config["auto_splits"])

	if "account_growth_partners" in config:
		# Sync account growth between partner accounts
		mm.sync_account_growth(config["account_growth_partners"])

	logger.info("Budgeting auto-processing via Monarch Money complete!")

if __name__ == "__main__":

	mint_wizard_dir = os.path.dirname(os.path.realpath(__file__))

	parser = argparse.ArgumentParser()
	parser.add_argument("-db", "--db-path", help="The path to the sqlite database file", default=f"{mint_wizard_dir}/mint-wizard.db", type=(lambda db_path: Db(db_path)), dest="db")
	parser.add_argument("-v", "--verbose", help="Display debug logs", action='store_true')
	subparsers = parser.add_subparsers(required=True)

	auto_process_parser = subparsers.add_parser("auto-process", help="Run the auto-processor")
	auto_process_parser.add_argument("-creds", "--credentials-path", help="The path to the file containing your credentials", required=True)
	auto_process_parser.add_argument("-short", "--shorthand-json-path", help="The path to the file containing the mapping of shorthand identifiers to categories", default=f"{mint_wizard_dir}/shorthands.json")
	auto_process_parser.add_argument("-names", "--splitwise-user-id-to-name-json", help="The path of the JSON file used to override names fetched from Splitwise")
	auto_process_parser.add_argument("-userid", "--custom-user-identifier", help="Turns on user-specific Splitwise flags. See README")
	auto_process_parser.add_argument("-config", help="Path to config file with recurring transactions and recategorizations", default=f"{mint_wizard_dir}/config.json")
	auto_process_parser.add_argument("-days", "--splitwise-days-to-look-back", help="The number of days to look back when determining expenses to process (script looks at updated dates, not dates of the expenses)", type=int, default=2)
	auto_process_parser.add_argument("--recurring-txns", help="Process recurring transactions", action=argparse.BooleanOptionalAction, default=True)
	auto_process_parser.add_argument("--recategorize-txns", help="Perform transaction recategorization", action=argparse.BooleanOptionalAction, default=True)
	auto_process_parser.add_argument("--splitwise", help="Process splitwise transactions", action=argparse.BooleanOptionalAction, default=True)	
	auto_process_parser.add_argument("--mm-session-pickle-file", help="The file to save cookies and auth tokens to for Monarch Money", default=f"{mint_wizard_dir}/mm_session.pickle")
	auto_process_parser.set_defaults(func=run_auto_processor)

	recurring_transactions_subparser = subparsers.add_parser("recurring-txns", help="Configure recurring transactions")
	recurring_transactions_subparsers = recurring_transactions_subparser.add_subparsers(required=True)

	recurring_transactions_subparsers.add_parser("list", help="List the configured recurring transactions").set_defaults(func=list_recurring_txns)
	
	add_recurring_txn_parser = recurring_transactions_subparsers.add_parser("add", help="Add a new recurring transaction")
	add_recurring_txn_parser.add_argument("-d", "--description", help="Description of the transaction", required=True)
	add_recurring_txn_parser.add_argument("-a", "--amount", help="Amount of the transaction. Negative numbers are charges, positive numbers are credits", required=True)
	add_recurring_txn_parser.add_argument("-c", "--category", help="Mint category for the transaction", required=True)
	add_recurring_txn_parser.add_argument("-r", "--recurrence-rule", help="Natural language representation of a recurring event. Can include start dates and end dates, but they are not required. Ex: \"every 2 weeks starting next monday until jan\", \"every day\"", dest="recurring_event", required=True, type=util.str_to_valid_recurring_event)
	add_recurring_txn_parser.add_argument("-short", "--shorthand-json-path", help="The path to the file containing the mapping of shorthand identifiers to categories", default=f"{mint_wizard_dir}/shorthands.json")
	add_recurring_txn_parser.add_argument("-mc", "--move-from-category", help="Category to move the transaction FROM. This will create two recurring transactions: one credit to the FROM category, and one charge to the -c category")
	add_recurring_txn_parser.set_defaults(func=add_recurring_txn)

	remove_recurring_txn_parser = recurring_transactions_subparsers.add_parser("remove", help="Remove a recurring transaction")
	remove_recurring_txn_parser.add_argument("-id", required=True)
	remove_recurring_txn_parser.set_defaults(func=remove_recurring_txn)

	args = parser.parse_args()

	if args.verbose:
		root_logger = logging.getLogger()
		root_logger.setLevel(logging.DEBUG)
		for handler in root_logger.handlers:
			handler.setLevel(logging.DEBUG)
		logger.debug("Verbose logging enabled")

	logger.debug(f"Args parsed: {args}")

	args.func(args)
