import pyotp
import sys
import asyncio
import logging
import requests

from requests.models import PreparedRequest

from decimal import Decimal
from db import get_next_occurrence_for_txn
from monarchmoney import MonarchMoney, RequireMFAException

logger = logging.getLogger(__name__)
logging.getLogger('gql.transport.aiohttp').setLevel(logging.WARN)

class MonarchMoneyHelper:
    def __init__(self, creds, db, session_file):
        self.creds = creds
        self.db = db

        self.mm = MonarchMoney(session_file = session_file)

        # login
        logger.info("Logging in...")
        try:
            asyncio.run(self.mm.login(creds['mm']['email'], creds['mm']['password']))
        except RequireMFAException:
            logger.debug("Providing TOTP...")
            asyncio.run(self.mm.multi_factor_authenticate(
                creds['mm']['email'], creds['mm']['password'], pyotp.TOTP(creds['mm']['totp_secret']).now()
            ))

        # Set up "Automated Transactions" account, if not present
        logger.info("Fetching 'Automated Transactions' dummy account...")
        result = asyncio.run(self.mm.get_accounts())
        filtered_accounts = list(filter(lambda x: x['displayName'] == "Automated Transactions", result['accounts']))

        if len(filtered_accounts) == 0:
            # TODO: set up account automatically
            logger.error("No 'Automated Transactions' dummy account exists. Please create one.")
            sys.exit(1)
        elif len(filtered_accounts) > 1:
            logger.error("More than one account exists with the name 'Automated Transactions'. Please rename extra accounts with that name.")
            sys.exit(1)
        else:
            self.automated_account_id = filtered_accounts[0]['id']

        logger.debug(f"'Automated Transactions' account fetched: {filtered_accounts[0]}")

        # Set up the category map
        logger.info("Fetching categories and setting up category -> id map...")
        self.category_map = {}
        result = asyncio.run(self.mm.get_transaction_categories())
        for category in result['categories']:
            if category['name'] in self.category_map:
                logger.error(f"Multiple categories with name '{category['name']}' exist. This may result in unintended behavior.")
            
            self.category_map[category['name']] = category['id']

        logger.debug(f"Categories fetched: {self.category_map}")

        # Set up the required tag(s), if not yet created
        logger.info("Fetching 'AUTOPROCESSED' tag...")
        result = asyncio.run(self.mm.get_transaction_tags())
        filtered_tags = list(filter(lambda tag: tag['name'] == "AUTOPROCESSED", result['householdTransactionTags']))

        if len(filtered_tags) == 0:
            # TODO: set up tag automatically
            logger.error("No 'Automated Transactions' dummy account exists. Please create one.")
            sys.exit(1)
        elif len(filtered_tags) > 1:
            logger.error("More than one tag exists with the name 'AUTOPROCESSED'. Please rename extra tags with that name.")
            sys.exit(1)
        else:
            self.autoprocessed_tag_id = filtered_tags[0]['id']

        logger.debug(f"AUTOPROCESSED tag fetched: {filtered_tags[0]}")

    # returns True if a transaction either was created or already existed
    def add_transaction(self, desc, price, category, date, dedupe, notes=""):
        price = Decimal(price)

        if price == 0:
            logger.warn("Given transaction %s has a value of 0. Skipping, and treating the transaction as created..." % (desc))
            return True

        dedupe_results = asyncio.run(self.mm.get_transactions(search = dedupe))
        if dedupe_results['allTransactions']['totalCount'] > 0:
            logger.info("Duplicate found: %s (dedupe string: %s). Skipping..." % (desc, dedupe))
            return True

        if category not in self.category_map:
            logger.error(f"Given category '{category}' does not exist in the user's account. Skipping...")
            return False

        logger.info("Adding transaction for \"%s\" with price $%s and category \"%s\"" % (desc, price, category))

        asyncio.run(self.mm.create_transaction(
            date.strftime("%Y-%m-%d"),
            self.automated_account_id,
            float(price),
            desc,
            self.category_map[category],
            dedupe if len(notes.trim()) == 0 else f"{notes}\n\nDEDUPE: {dedupe}"))

        return True

    def search_transactions(self, **kwargs):
        return asyncio.run(self.mm.get_transactions(**kwargs))['allTransactions']['results']

    def recategorize_txn(self, txn, category, description=False, set_as_autoprocessed=True):
        raise NotImplementedError

    def tag_txn(self, txn, tag):
        raise NotImplementedError


    def recategorize_all_txns(self, txns, category, set_as_autoprocessed=True):
        for txn in txns:
            self.recategorize_txn(txn, category, set_as_autoprocessed)


    def get_txn_statement_name(self, txn):
        raise NotImplementedError
        return txn.get_attribute("title")[len("Statement Name: "):]

    def recategorize_target_transactions(self, pattern_configs):
        raise NotImplementedError
        logger.info("Starting to recategorize target transactions by pattern")

        self.wait_for_transaction_table(hide_autoprocessed=True)

        for pattern, category, new_description in pattern_configs:
            logger.info("Processing pattern /%s/; recategorizing matches to \"%s\" with new description \"%s\"" % (pattern, category, new_description))

            txns = self.get_all_transactions()
            txns.reverse()

            txns = list(filter(lambda txn: re.search(pattern, self.get_txn_statement_name(txn)), txns))

            logger.info("%s matches found for pattern" % len(txns))
            for txn in txns:
                statement_name = self.get_txn_statement_name(txn)
                if "AUTOCATEGORIZED" in statement_name:
                    logger.info("Skipping matching transaction \"%s\" as it's already auto-categorized" % statement_name.replace(" | AUTOCATEGORIZED", "").strip())
                    continue

                logger.info("Renaming matching transaction \"%s\" to \"%s\", and recategorizing as \"%s\"" % (statement_name, new_description, category))
                self.recategorize_txn(txn, category, description="%s | AUTOCATEGORIZED" % new_description)
                logger.info("Transaction recategorized")

    def process_recurring_transactions(self):
        logger.info("Processing recurring transactions")

        txns = self.db.get_past_due_recurring_transactions()
        logger.info("%s recurring transactions to process in first iteration" % len(txns))
        skipped_ids = set()
        while txns:
            for txn in txns:
                logger.info("Creating transaction for \"%s\"" % txn)

                next_occurrence = get_next_occurrence_for_txn(txn)
                if self.add_transaction(txn.description, txn.amount, txn.category, next_occurrence, "RECUR:%s:%s" % (txn.dedupe_string, next_occurrence.isoformat()), notes=txn.notes):
                    # only run the completion logic if the transaction now exists
                    self.db.process_recurring_transaction_completion(txn.id)
                else:
                    skipped_ids.add(txn.id)

            txns = self.db.get_past_due_recurring_transactions(exclude_ids=skipped_ids)
            logger.info("%s recurring transactions to process in next iteration" % len(txns))

    def export_account_balances(self, webhook):
        logger.info("Starting account balance export")

        # fetch all account balances
        accounts = asyncio.run(self.mm.get_accounts())
        extra_params = {account['displayName']: account['currentBalance'] for account in accounts['accounts']}

        # aggregate all cost basis for taxable brokerage accounts
        brokerages = list(filter(lambda account: account['subtype']['display'] == "Brokerage (Taxable)" and not account['isHidden'], accounts['accounts']))
        cost_basis = 0
        for brokerage in brokerages:
            holdings = asyncio.run(self.mm.get_account_holdings(brokerage['id']))
            for holding in holdings['portfolio']['aggregateHoldings']['edges']:
                cost_basis += holding['node']['basis']

        if "Roth Contribution" in self.category_map:
            # Fetch all roth contributions
            roth_contributions = asyncio.run(self.mm.get_transactions(category_ids = [self.category_map["Roth Contribution"]]))

            extra_params["Roth Contributions"] = sum([abs(txn["amount"]) for txn in roth_contributions["allTransactions"]["results"]])

        if "Roth Conversion" in self.category_map:
            # Fetch all roth conversions
            roth_conversions = asyncio.run(self.mm.get_transactions(category_ids = [self.category_map["Roth Conversion"]]))

            extra_params["Roth Conversions"] = sum([abs(txn["amount"]) for txn in roth_conversions["allTransactions"]["results"]])

        extra_params['Taxable Cost Basis'] = cost_basis

        # send the results to the given webhook
        req = PreparedRequest()
        req.prepare_url(webhook, extra_params)

        requests.get(req.url)
