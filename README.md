## Splitwise Description Flags

### Mint Transaction Flag

Adding one of these flags to the splitwise item description instructs the script
to add the given expense to your Mint transactions log.

It is expected that the flag is in the following format:

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