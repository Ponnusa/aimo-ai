package aimo.supplier.v1

# Food-wholesale supplier policy (M5). Independent of the buyer's policy;
# evaluated against this organization's own bundle only.

default decision := {
	"allow": false,
	"human_approval_required": false,
	"deny_reason": "unsupported_action",
}

decision := result if {
	input.action == "authorize_agreement"
	result := authorize_result
}

total_amount := to_number(input.terms.total_amount)

human_approval_threshold := to_number(data.human_approval_threshold)

max_order_quantity := to_number(data.max_order_quantity)

approved_customer if {
	input.counterparty_organization_id == data.approved_customers[_]
}

quantity_exceeded if {
	some line in input.terms.lines
	to_number(line.quantity) > max_order_quantity
}

authorize_result := {
	"allow": false,
	"human_approval_required": false,
	"deny_reason": sprintf("customer %v is not an approved customer", [input.counterparty_organization_id]),
} if {
	not approved_customer
}

authorize_result := {
	"allow": false,
	"human_approval_required": false,
	"deny_reason": sprintf("order quantity exceeds max_order_quantity (%v) for one or more lines", [max_order_quantity]),
} if {
	approved_customer
	quantity_exceeded
}

authorize_result := {
	"allow": true,
	"human_approval_required": true,
	"deny_reason": null,
} if {
	approved_customer
	not quantity_exceeded
	total_amount > human_approval_threshold
}

authorize_result := {
	"allow": true,
	"human_approval_required": false,
	"deny_reason": null,
} if {
	approved_customer
	not quantity_exceeded
	total_amount <= human_approval_threshold
}
