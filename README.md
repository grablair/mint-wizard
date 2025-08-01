# Mint Wizard

Mint Wizard provides several features that can be used to programmatically access
and modify data in your Mint account.

The primary feature of Mint Wizard integrates Mint with
[Splitwise](https://splitwise.com), so your expenses on Splitwise can be
auto-imported into Mint, without needing to manually split transactions in Mint
later. 

It also allows you set up recurring transactions in Mint, using the CLI.

It also allows you to recategorize transactions based on a regex pattern of the
transaction's description.

It uses Selenium to simulate actual browser actions on the Mint website, as the
Mint API is closed-source.

Also helpful for Mint QoL improvements is my [Mint Helper userscript](https://gist.github.com/grablair/8f83e2916b815e24d67bd49fd43158f6).
Information on it's features can be found in the [userscript](#mint-helper-userscript)
section below.

## Splitwise Integration

Splitwise integration works by adding certain flags to the end of your Splitwise
expense descriptions.

### Mint Transaction Flag

Adding one of these flags to the splitwise item description instructs the script
to add the given expense to your Mint transactions log.

It is expected that the flag be in the following format:

```
M[modifiers]:<shorthand-key>
```

A set of default mappings from `<shorthand-key>`'s' to Mint categories can be
found in the `shorthands.json` file.

This file can be overridden on the command line. This is particularly useful if
there are two people who both use this script and often have expenses together,
but would like to categorize their expenses differently in Mint. These users
would simply provide separate shorthand JSON files with different mappings for
the same shorthand keys.

There can be any number of modifiers specified before the `:`. Modifiers are
always one capital letter each. They are described below:

1.  The `C` modifier indicates that the amount that any user **paid** in the 
    expense should also be added as a charge to that user, before adding the
    repayment. This can be helpful in a few ways:

    * You have a payment plan set up through your credit card that you split the
       monthly charges with, so you want to track the monthly payment as a "charge"
    * You paid for something with cash that you wish to split, so you want to
      track the initial charge as well.
    * You paid for something with a cash-like app (Venmo, Square, etc) that is
      hard to set up categorization rules for, as transactions through those
      services all look the same to Mint. This way, you can keep those transactions
      set to "Transfer" or "Hide from Budgets & Trends", and still track the initial
      charge.

    Example of a usecase:

	Alice and Bob both use the mint-autoprocessor script. Every now and again, Bob pays
	the neighbor kid to mow the lawn in cash. Bob wants to split this cost with Alice,
	so he adds a transaction of $50 to Splitwise with the following description:

	```
	Lawn Mowing MC:LAWN
	```

	In this case, Bob would have a $25 **credit** added to his Mint transactions, and
	Alice would have a $25 **charge** added to her Mint transactions. Additionally, Bob
	would have a $50 **charge** added to his Mint transactions, resulting in an effective
	**charge** of $25 for both Alice and Bob.

	If Bob didn't include the `C` flag, then he would be sitting with an effective $25 
	credit for the lawn mowing, despite paying for half of the expense.

### Delay Action Flag

Adding this flag will delay the action on the given Splitwise transaction by however
many days are specified. It does this by scheduling a one-time "recurring" transaction
for the target day.

It is expected that the flag be in the following format:

```
D[user-identifier]:<days-to-delay-integer>
```

This is useful in situations where you want to collect money now for an expense that is
going to occur in future.

You can specify a User-Identifier here that will work the same way as the custom user
specific mint transaction flags in the next section

Example of a usecase:

Bob and Alice are roommates. Alice pays the rent every month on the 5th, and collects
rent from Bob before that by adding an recurring expense in Splitwise that recurs two
weeks prior to the due date. That gives Bob two weeks to pay Alice before the payment
is actually due. If Alice just put the expense in normally, the expense would be
imported into Mint two weeks before it was due: in the month _prior_ to when it will
be paid. Her Mint budgets would go into the negatives (note that negatives means
credit in this context) for those two weeks, giving her an inaccurate picture of her
overall budget status. By delaying action on the expense, she can have the actual rent
payment and the reimbursement register on the same day in Mint, keeping her day-to-day
overall budget balance somewhat consistent, and ensuring that related transactions in
Mint occur on or around the same day, while still giving Bob two weeks heads up on the
charge.


### Custom User-Specific Mint Transaction Flag

This flag is used in tandem with the `--mint-custom-user-identifier` CLI
argument. Without that argument, this flag will be ignored.

This is an optional flag that allows you to **modify** the effects of the standard
Mint transaction flag for the current user, but only if the provided identifier
matches the identifier in the flag.

The user identifier can be **any number of upper-case letters**. Make it as unique
as you need to, dependent upon the number of friends you have on Splitwise, and the
likelihood of them using this script _and_ colliding with your chosen identifier.

The flag is in the following format:

```
U<user-identifier>:<modifier(s)>
```

The `<modifier(s)>` are any number of compatible single-letter modifiers to invoke
for the current user.

1.  The `C` modifier behaves exactly the same way as the `C` global modifier, except
	it only applies to a single user.

	This is useful if multiple people paid for an expense, but only a **subset** of
	those people paid with a non-trackable medium (e.g. cash, cash-equivalent apps).

	Example of the use of a user-specific flag:

	Alice, Bob, and Charles all use the mint-autoprocessor script. They went to dinner
	together, for which Alice paid the $50 bill with her credit card. Bob tipped for
	everyone in cash, as he was the only one with cash, and knows that cash is always
	better for the service staff when it comes to tipping. In order to properly track
	this in Splitwise, they add a transaction where Alice paid $50, and Bob paid $10.
	To ensure this is tracked in Mint properly, they use the following description:

	```
	Indian Food M:<RESTAURANT_SHORTHAND> UBOB:C
	```

	In this case, the cost per person is $20. Alice would have a $30 **credit** added
	to her Mint transactions, Bob would have a $10 **charge** added to his Mint
	transactions, and Charles would have a $20 charge added to his Mint transactions.

	Additionally, Bob would have a $10 **charge** added to his Mint transactions,
	resulting in an effective **charge** of $20 in Mint for both Alice and Bob.

	Their transactions in Mint would look like:

	```
	                  Alice |  Bob | Charles
	CC Charge:        -$50  |  $0  |  $0
	Script Deb/Cre:   +$30  | -$10 | -$20
	Extra Charge:      $0   | -$10 |  $0
	----------------------------------------
 	Result:           -$20  | -$20 | -$20
	```

	If they didn't include the `UBOB:C` flag, or if Bob didn't specify 
	`--mint-custom-user-identifier BOB` in his CLI arguments, then he would be tracking
	just $10 for the expense: the $10 added by this script as what he "owed" in Splitwise,
	but not the $10 "Extra Charge".

	If they simply used the global `C` flag, Alice would have the $50 credit card charge,
	_another_ $50 automated charge would be added by the script, and then a $30 credit,
	resulting in $70 being tracked in Mint.
2.  The `S` modifier skips this transaction for the current user.

## Recurring Transactions

You can create recurring transactions, using natural language to specify the
frequency of recurrence.

### Add Recurring Transaction

To add a recurring transaction, run:

```
mint-wizard.py recurring-txns \
    -d <description> \
    -a <amount (negative for a charge)> \
    -c <category (full or shorthand)> \
    -r <natural-language-recurrence-rule> \
    [-mc <move-from-category>] \
    [-short <shorthand-mapping-file-override>]
```

The `-r / --recurrence-rule` argument is a natural language representation of a
recurring event. Some examples are "every 2 weeks starting next monday until jan",
"every day", and "10th of every month at 12am starting Aug 9, 2023".

The `-mc / --move-from-category` argument allows for easy moving from one category
to another. For example, if you specify `-a "-100"` `-c Entertainment` and
`-mc "Electronics & Software"`, two recurrence rules will be created. One that is
in the category `Entertainment` for the specified `-100` and one in the
`Electronics & Software` category, for `100`, the inverse. This effectively "moves"
$100 from `Electronics & Software` to `Entertainment`.

The `-short` argument allows you to override the path to the shorthands mapping file.

### List Recurring Transactions

To list all recurring transactions, run:

```
mint-wizard.py recurring-txns list
```

### Remove Recurring Transaction

To remove a recurring transaction, run:

```
mint-wizard.py recurring-txns remove -id <recurring-txn-id>
```

You can obtain the ID of a recurring transaction from the `recurring-txns list` command.

## Recategorization

Recategorization rules can be added to the [config file](config.json). The are configured
like the following:

```
{
	"patterns_to_recategorize": [
		["<description-regex>", "<new-category>", "<new-description>"],
		["^Frankie and Jos.* ", "Fast Food", "Frankie and Jo's Ice Cream"] // example
	]
}
```

## Mint Helper Userscript

The [Mint Helper userscript](https://gist.github.com/grablair/8f83e2916b815e24d67bd49fd43158f6)
can be installed via the Greasemonkey or Tampermonkey browser extensions. It has only been
tested on Chrome using Tampermonkey.

It's features are:

1. A revamped Budgets view, including:
   1. Reordering of budgets
   2. Creation of custom dividers, which can also be ordered
   3. Auto-hiding of budgets that are "complete" (either has $1 or $0 left, or is a
     "set-aside" budget for the month)
2. Replacement of the dumb overview budgets view with a full budgets view (so you really
   only need to look at the overview to get the full picture)
3. Auto-hiding of transactions with $0 values
4. Auto-removal of all "offers" (that aren't technically ads and don't get caught by
   the various ad-blocking extensions)
