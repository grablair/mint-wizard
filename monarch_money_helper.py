import pyotp
import sys
import asyncio
import logging
import requests

from requests.models import PreparedRequest

from decimal import Decimal
from db import get_next_occurrence_for_txn
from monarchmoney import MonarchMoney, RequireMFAException
from datetime import datetime, timedelta, date

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
        logger.info("Fetching accounts...")
        result = asyncio.run(self.mm.get_accounts())

        self.account_map = {}
        for account in result['accounts']:
            if account['displayName'] in self.account_map:
                if account['displayName'] is "Automated Transactions":
                    logger.error("More than one account exists with the name 'Automated Transactions'. Please rename extra accounts with that name.")
                    sys.exit(1)
                else:
                    logger.error(f"Multiple accounts with name '{category['name']}' exist. This may result in unintended behavior.")

            self.account_map[account['displayName']] = account['id']

        logger.debug(f"Accounts fetched: {self.account_map}")

        if "Automated Transactions" not in self.account_map:
            # TODO: set up account automatically
            logger.error("No 'Automated Transactions' dummy account exists. Please create one.")
            sys.exit(1)
        else:
            self.automated_account_id = self.account_map['Automated Transactions']

        logger.debug(f"'Automated Transactions' account found: {filtered_accounts[0]}")

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
            dedupe if (notes == None or len(str(notes).strip()) == 0) else f"{notes}\n\nDEDUPE: {dedupe}"))

        return True

    def search_transactions(self, **kwargs):
        return asyncio.run(self.mm.get_transactions(**kwargs))['allTransactions']['results']

    def get_budgets(self, **kwargs):
        return asyncio.run(self.mm.get_budgets(**kwargs))['budgetData']['monthlyAmountsByCategory']

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

    def handle_auto_splits(self, auto_splits):
        if auto_splits is None or len(auto_splits) == 0:
            return

        logger.info("Handling auto-splits")

        today = date.today()
        start_of_last_month = (today - timedelta(days=today.day)).replace(day=1)
        end_of_last_month = today - timedelta(days=today.day)

        self.handle_auto_splits_for_dates(auto_splits, start_of_last_month, end_of_last_month)

        start_of_this_month = today.replace(day=1)
        next_month_sometime = today.replace(day=28) + timedelta(days=4)
        end_of_this_month = next_month_sometime - timedelta(days=next_month_sometime.day)

        self.handle_auto_splits_for_dates(auto_splits, start_of_this_month, end_of_this_month)

    def handle_auto_splits_for_dates(self, auto_splits, start_date, end_date):
        budgets = self.get_budgets(
            start_date = start_date.strftime("%Y-%m-%d"),
            end_date = end_date.strftime("%Y-%m-%d"))

        for auto_split in auto_splits:
            txns = self.search_transactions(
                search = auto_split['description'],
                start_date = start_date.strftime("%Y-%m-%d"),
                end_date = end_date.strftime("%Y-%m-%d"),
                is_split = False)

            def handle_txn_in_auto_split(txn):
                for condition in auto_split['conditions']:
                    match condition['rule']:
                        case "greaterThan"          if abs(txn['amount']) <= abs(condition['amount']):
                            return
                        case "greaterThanOrEqualTo" if abs(txn['amount']) <  abs(condition['amount']):
                            return
                        case "lessThan"             if abs(txn['amount']) >= abs(condition['amount']):
                            return
                        case "lessThanOrEqualTo"    if abs(txn['amount']) >  abs(condition['amount']):
                            return
                        case "equals"               if abs(txn['amount']) != abs(condition['amount']):
                            return

                logger.info(f"Handling auto-split {auto_split}")

                credit_debit_modifier = -1 if txn['amount'] < 0 else 1
                splits = []
                for split in auto_split['splits']:
                    if 'budget_directed' in split and split['budget_directed']:
                        budget = next(budget for budget in budgets if budget['category']['id'] == self.category_map[split['category']])
                        amount = abs(budget['monthlyAmounts'][0]['plannedCashFlowAmount'])
                    else:
                        amount = abs(split['amount'])

                    splits.append({
                            "merchantName": split['description'],
                            "amount": credit_debit_modifier * amount,
                            "categoryId": self.category_map[split['category']]
                        })

                remainder = txn['amount'] - sum([split['amount'] for split in splits])
                splits.append({
                        "merchantName": txn['merchant']['name'],
                        "amount": remainder,
                        "categoryId": txn['category']['id']
                    })

                logger.info(f"Splitting ${txn['amount']} transaction for {txn['merchant']['name']} into sections {splits}")

                res = asyncio.run(self.mm.update_transaction_splits(txn['id'], splits))

                logger.info(f"Split successful: {res}")

            for txn in txns:
                handle_txn_in_auto_split(txn)

    def sync_account_growth(self, partner_account_mapping):
        today = date.today()
        yesterday = today - timedelta(days=1)

        logger.info("Syncing account growth between partner accounts...")

        for partner_account_mapping in partner_account_mapping:
            child_account = partner_account_mapping['child_account']
            parent_account = partner_account_mapping['parent_account']

            logger.info(f"Syncing the balance of \"{child_account}\" to the tracked account \"{parent_account}\"")
            parent_account_history = asyncio.run(self.mm.get_account_history(self.account_map[parent_account]))

            yesterday_balance = next(snapshot['signedBalance'] for snapshot in parent_account_history if snapshot['date'] == yesterday.strftime("%Y-%m-%d"))
            today_balance = next(snapshot['signedBalance'] for snapshot in parent_account_history if snapshot['date'] == today.strftime("%Y-%m-%d"))

            difference = today_balance - yesterday_balance

            if difference != 0:
                logger.info(f"Difference found between yesterday and today: ${difference:.2f}")

                child_account_history = asyncio.run(self.mm.get_account_history(self.account_map[child_account]))
                child_balance_yesterday = next(snapshot['signedBalance'] for snapshot in child_account_history if snapshot['date'] == yesterday.strftime("%Y-%m-%d"))

                new_child_account_balance = child_balance_yesterday * (difference / yesterday_balance)

                child_account_txns_today = asyncio.run(self.mm.get_transactions(
                    account_ids=[str(self.account_map[child_account])],
                    start_date=today.strftime("%Y-%m-%d"),
                    end_date=today.strftime("%Y-%m-%d")
                ))['allTransactions']['results']

                new_child_account_balance += sum([txn['amount'] for txn in child_account_txns_today])

                logger.info(f"New child account balance: {new_child_account_balance:.2f}")

                asyncio.run(self.mm.update_account(child_account, account_balance=new_child_account_balance))
            else:
                logger.info(f"No change in tracked account balance found")
