package aimo.buyer.v1

# Food-wholesale buyer policy (M5). Deterministic, versioned, independent
# of the supplier's own policy. Input/output contract is generic
# (packages/policy_engine); only the data below (budget, approved
# suppliers, human-approval threshold) is food-wholesale specific.

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

budget_limit := to_number(data.budget_limit)

human_approval_threshold := to_number(data.human_approval_threshold)

approved_supplier if {
	input.counterparty_organization_id == data.approved_suppliers[_]
}

over_budget if {
	total_amount > budget_limit
}

authorize_result := {
	"allow": false,
	"human_approval_required": false,
	"deny_reason": sprintf("supplier %v is not an approved supplier", [input.counterparty_organization_id]),
} if {
	not approved_supplier
}

authorize_result := {
	"allow": false,
	"human_approval_required": false,
	"deny_reason": sprintf("total amount %v exceeds budget limit %v", [total_amount, budget_limit]),
} if {
	approved_supplier
	over_budget
}

authorize_result := {
	"allow": true,
	"human_approval_required": true,
	"deny_reason": null,
} if {
	approved_supplier
	not over_budget
	total_amount > human_approval_threshold
}

authorize_result := {
	"allow": true,
	"human_approval_required": false,
	"deny_reason": null,
} if {
	approved_supplier
	not over_budget
	total_amount <= human_approval_threshold
}
