import pyotp
import sys
import asyncio
import logging

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
        try:
            asyncio.run(self.mm.login(creds['mm']['email'], creds['mm']['password']))
        except RequireMFAException:
            asyncio.run(self.mm.multi_factor_authenticate(
                creds['mm']['email'], creds['mm']['password'], pyotp.TOTP(creds['mm']['totp_secret']).now()
            ))

        # Set up "Automated Transactions" account, if not present
        result = asyncio.run(self.mm.get_accounts())
        filtered_accounts = list(filter(lambda x: x['displayName'] == "Automated Transactions", result['accounts']))

        if len(filtered_accounts) == 0:
            pass
            # TODO: set up account
        elif len(filtered_accounts) > 1:
            logger.error("More than one account exists with the name 'Automated Transactions'. Please rename extra accounts with that name.")
            sys.exit(1)
        else:
            self.automated_account_id = filtered_accounts[0]['id']

        # Set up the category map
        self.category_map = {}
        result = asyncio.run(self.mm.get_transaction_categories())
        for category in result['categories']:
            if category['name'] in self.category_map:
                logger.error(f"Multiple categories with name '{category['name']}' exist. This may result in unintended behavior.")
            
            self.category_map[category['name']] = category['id']

        logger.info(f"Categories fetched: {self.category_map}")

        # Set up the required tag(s), if not yet created
        result = asyncio.run(self.mm.get_transaction_tags())
        filtered_tags = list(filter(lambda tag: tag['name'] == "AUTOPROCESSED", result['householdTransactionTags']))

        if len(filtered_tags) == 0:
            # TODO: set up 
            pass
        elif len(filtered_tags) > 1:
            logger.error("More than one tag exists with the name 'AUTOPROCESSED'. Please rename extra tags with that name.")
            sys.exit(1)
        else:
            self.autoprocessed_tag_id = filtered_tags[0]['id']

    def add_transaction(self, desc, price, category, date, dedupe, notes=""):
        price = Decimal(price)

        if price == 0:
            return

        dedupe_results = asyncio.run(self.mm.get_transactions(search = dedupe, has_notes=True))
        if dedupe_results['allTransactions']['totalCount'] > 0:
            logger.info("Duplicate found: %s (dedupe string: %s). Skipping..." % (desc, dedupe))
            return

        if category not in self.category_map:
            logger.error(f"Given category '{category}' does not exist in the user's account. Skipping...")
            return

        logger.info("Adding transaction for \"%s\" with price $%s and category \"%s\"" % (desc, price, category))

        asyncio.run(self.mm.create_transaction(
            date.strftime("%Y-%m-%d"),
            self.automated_account_id,
            float(price),
            f"{desc} | {dedupe}",
            self.category_map[category],
            notes))


    def recategorize_txn(self, txn, category, description=False, set_as_autoprocessed=True):
        raise NotImplementedError
        self.hide_account_status_bar()
        self.get_elem_by_automation_id('EDIT_TRANSACTION_LINK', elem=txn).click()

        if description:
            current_description = self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').get_attribute("value")
            
            clear_text = ""
            for i in range(len(current_description)):
                clear_text = clear_text + Keys.BACKSPACE

            self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys(clear_text)
            self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys("{} ({})".format(description, current_description))


        self.fill_category_dropdown(category)

        if set_as_autoprocessed:
            self.get_elem_by_automation_id('SELECT_A_TAG').click()
            self.get_elem_by_automation_id('TAG_CHOICE_AUTOPROCESSED').click()

        self.driver.execute_script('arguments[0].scrollIntoView({block: "center"});', self.get_elem_by_automation_id('SAVE'))

        self.get_elem_by_automation_id('SAVE').click()
        self.wait_for_edit_txn_to_close()

    def tag_txn(self, txn, tag):
        raise NotImplementedError
        self.get_elem_by_automation_id('EDIT_TRANSACTION_LINK').click()
        self.get_elem_by_automation_id('SELECT_A_TAG').click()
        self.get_elem_by_automation_id('TAG_CHOICE_{}'.format(tag)).click()
        self.get_elem_by_automation_id('SAVE').click()
        self.wait_for_edit_txn_to_close()


    def recategorize_all_txns(self, txns, category, set_as_autoprocessed=True):
        raise NotImplementedError
        for txn in txns:
            self.recategorize_txn(txn, category, set_as_autoprocessed)


    def get_txn_statement_name(self, txn):
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
        while txns:
            for txn in txns:
                logger.info("Creating transaction for \"%s\"" % txn)

                next_occurrence = get_next_occurrence_for_txn(txn)
                self.add_transaction(txn.description, txn.amount, txn.category, next_occurrence, "RECUR:%s:%s" % (txn.dedupe_string, next_occurrence.isoformat()), notes=txn.notes)
                self.db.process_recurring_transaction_completion(txn.id)
            txns = self.db.get_past_due_recurring_transactions()
            logger.info("%s recurring transactions to process in next iteration" % len(txns))