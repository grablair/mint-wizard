#!/usr/local/bin/python3

import re
import json
import sys
import argparse
import logging
import time

from datetime import datetime, timedelta
from pytimeparse.timeparse import timeparse

from splitwise_helper import SplitwiseHelper
from mint_helper import MintHelper
from db import Db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def list_recurring_txns(args):
	logging.info("Listing recurring transactions")
	[print(txn) for txn in args.db.get_all_recurring_transactions()]

def add_recurring_txn(args):
	logging.info("Adding recurring transaction")
	args.db.create_recurring_transaction(args.description, args.amount, args.category, args.frequency, args.first_occurrence, args.stop_after)

def remove_recurring_txn(args):
	logging.info("Removing recurring transaction")
	args.db.remove_recurring_transaction(args.id)

def run_auto_processor(args):
	logging.info("Starting run of the Mint Auto-Processor")

	if args.mint_custom_user_identifier and not re.match(r'^[A-Z]+$', args.mint_custom_user_identifier):
		print("--mint-custom-user-identifier must be solely positive letters from A-Z. It was: {}".format(args.mint_custom_user_identifier))
		sys.exit()

	creds = json.load(open(args.credentials_path))
	config = json.load(open(args.config))

	mint = MintHelper(creds, args.chromedriver_path)
	splitwise = SplitwiseHelper(creds, mint, args.shorthand_json_path, args.splitwise_user_id_to_name_json, args.mint_custom_user_identifier)

	if "patterns_to_recategorize" in config:
		mint.recategorize_target_transactions(config["patterns_to_recategorize"])
	
	splitwise.process_splitwise_expenses()


	mint.close()

	logging.info("Mint auto-processing complete!")

if __name__ == "__main__":

	parser = argparse.ArgumentParser()
	parser.add_argument("-db", "--db-path", help="The path to the sqlite database file", default="./mint-wizard.db", type=(lambda db_path: Db(db_path)), dest="db")

	subparsers = parser.add_subparsers(required=True)

	auto_process_parser = subparsers.add_parser("auto-process", help="Run the auto-processor")
	auto_process_parser.add_argument("-creds", "--credentials-path", help="The path to the file containing your credentials", required=True)
	auto_process_parser.add_argument("-chrome", "--chromedriver-path", help="The path to the ChromeDriver for Selenium to use", required=True)
	auto_process_parser.add_argument("-short", "--shorthand-json-path", help="The path to the file containing the mapping of shorthand identifiers to mint categories", default="./shorthands.json")
	auto_process_parser.add_argument("-names", "--splitwise-user-id-to-name-json", help="The path of the JSON file used to override names fetched from Splitwise")
	auto_process_parser.add_argument("-mintid", "--mint-custom-user-identifier", help="Turns on user-specific Splitwise flags. See README")
	auto_process_parser.add_argument("-config", help="Path to config file with recurring transactions and recategorizations", default="./config.json")
	auto_process_parser.set_defaults(func=run_auto_processor)

	recurring_transactions_subparser = subparsers.add_parser("recurring-txns", help="Configure recurring Mint transactions")
	recurring_transactions_subparsers = recurring_transactions_subparser.add_subparsers(required=True)

	recurring_transactions_subparsers.add_parser("list", help="List the configured recurring transactions").set_defaults(func=list_recurring_txns)
	
	add_recurring_txn_parser = recurring_transactions_subparsers.add_parser("add", help="Add a new recurring transaction")
	add_recurring_txn_parser.add_argument("-d", "--description", help="Description of the transaction", required=True)
	add_recurring_txn_parser.add_argument("-a", "--amount", help="Amount of the transaction. Negative numbers are charges, positive numbers are credits", required=True)
	add_recurring_txn_parser.add_argument("-c", "--category", help="Mint category for the transaction", required=True)
	add_recurring_txn_parser.add_argument("-f", "--frequency", help="Frequency of recurrence. Ex: 7d", required=True, type=(lambda frequency: timedelta(timeparse(frequency))))
	add_recurring_txn_parser.add_argument("-fd", "--first-occurrence", help="String representation of the first occurrence, in the following format: YYYY-MM-DD HH:MM", required=True, type=lambda s: datetime.strptime(s, '%Y-%m-%d %H:%M'))
	add_recurring_txn_parser.add_argument("-sd", "--stop-after", help="Optional string representation of the ending datetime for recurrence, in the following format: YYYY-MM-DD HH:MM", type=lambda s: datetime.strptime(s, '%Y-%m-%d %H:%M'))
	add_recurring_txn_parser.set_defaults(func=add_recurring_txn)

	remove_recurring_txn_parser = recurring_transactions_subparsers.add_parser("remove", help="Remove a recurring transaction")
	remove_recurring_txn_parser.add_argument("-id", required=True)
	remove_recurring_txn_parser.set_defaults(func=remove_recurring_txn)

	args = parser.parse_args()
	args.func(args)

# def add_recurring_transactions(recurring_txns):
# 	pass

# TODO: Move these entries elsewhere
# def handle_apple_monthly_installments():
# 	""" Hides / recategorizes auto-recurring Apple Card monthly installments """

# 	clear_search_filters()
# 	wait_for_transaction_table()

# 	instances_to_recategorize = [
# 		(Decimal("-17.87"), "Apple Watch Payment", "Mobile Phone", date.fromisoformat("2023-11-10")),
# 		(Decimal("-22.87"), "iPhone 13 Pro Payment", "Mobile Phone", date.fromisoformat("2023-09-10")),
# 		(Decimal("-32.87"), "iPhone 14 Pro Payment", "Mobile Phone", date.fromisoformat("2024-09-10")),
# 		(Decimal("-45.79"), "Courtney's Phone", HIDE_CATEGORY, date.fromisoformat("2023-09-10")),
# 		(Decimal("-11.20"), "Courtney's Insurance", HIDE_CATEGORY, date.fromisoformat("2023-09-10")),
# 		(Decimal("-45.79"), "Kevin's Phone", HIDE_CATEGORY, date.fromisoformat("2023-09-10")),
# 		(Decimal("-11.20"), "Kevin's Insurance", HIDE_CATEGORY, date.fromisoformat("2023-09-10"))
# 	]

# 	txns = get_elems_by_description("Monthly Installments")
# 	txns.reverse()

# 	# TODO: group these by date, make properly idempotent
# 	for txn in txns:
# 		raw_price = txn.find_element(By.CSS_SELECTOR, '[class*="StyledComponents__TransactionAmount"]').text
# 		price = Decimal(raw_price.replace("$", "")) 

# 		for instance_price, desc, category, end_date in list(instances_to_recategorize):
# 			if end_date < date.today():
# 				logging.debug("Apple Card installment {} has expired with end date of {}".format(desc, end_date))
# 				continue

# 			if price == instance_price:
# 				instances_to_recategorize.remove((instance_price, desc, category))
# 				recategorize_txn(txn, category, description=desc)
# 				break
# 		else:
# 			logging.warning("Unknown Apple Card installment plan with price ${}".format(math.abs(instance_price)))
# 			tag_txn(txn, NEEDS_ATTENTION_TAG)


# def handle_paycheck():
# 	txns = get_elems_by_description("External Deposit - AMAZON DEV")

# 	for txn in txns:
# 		logging.info("Changing category for paycheck received on date: {}".format(txn.find_element(By.CSS_SELECTOR, 'td:nth-child(2) div').text))
# 		recategorize_txn(txn, "Paycheck", description="Amazon Paycheck")
