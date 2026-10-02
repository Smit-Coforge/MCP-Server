# IT equipment requests

The server decides from policy records. Each piece of equipment has its own limits, and those limits also differ by employment type. The same evaluation steps run for every item, so adding a headset or an external SSD does not add a new branch. The agent does not reimplement those steps. It calls the MCP tools and follows what they return.

The policy clock is fixed at **2026-10-01**. Age is whole years against that day. A monitor issued on 2023-10-01 is 3 years old. One issued on 2023-10-02 is still inside a 3-year cadence.

## Request fields

The employee message supplies only these four fields. Role, employment type, tenure, and current gear come from `get_employee_info`. They are not request fields.

| Field | Type | Required | Values |
|---|---|---|---|
| `employee_id` | string | yes | Id in the employee file |
| `item` | string | yes | A key in the catalog below, or anything else |
| `reason` | string | yes | `broken`, `lost`, `new_hire`, `preference`, `performance` |
| `quantity` | integer | yes | Whole number, 1 or greater |

A manager name, a needed-by date, or an asset tag in the message is ignored. Mice, keyboards, external SSDs, and webcams are not tracked assets and have no tag.

## Employee record

`get_employee_info` returns two independent fields besides tenure and gear.

| Field | Values | What it controls |
|---|---|---|
| `role` | `standard`, `manager` | Whether the catalog row is available at all. A laptop requires `manager`. |
| `employment_type` | `fulltime`, `intern`, `contractor` | Which max quantity and cadence apply once the row is available. |

An intern manager is allowed by the file. The laptop row still requires `manager`, and the intern column on that row is empty, so the request is denied. A contractor who is `standard` cannot receive a laptop either.

## Catalog

This table is the data, not a decision tree. A dash means that employment type has no limit for the item. `required_role` of `manager` means `standard` never receives the row, regardless of employment type.

`max_quantity` is the most that employment type may ask for in one request. `cadence_years` applies only to tracked items: another request is clearly allowed when there is no unit on file, or when the newest `issued_on` is at least that many whole years before the policy clock. Untracked items are not stored on the employee and have no cadence.

| Item | Kind | Required role | Full-time max | Full-time cadence | Intern max | Intern cadence | Contractor max | Contractor cadence |
|---|---|---|---|---|---|---|---|---|
| `monitor` | tracked | — | 2 | 3 years | 1 | 3 years | 1 | 2 years |
| `laptop` | tracked | `manager` | 1 | 2 years | — | — | 1 | 2 years |
| `dock` | tracked | — | 1 | 4 years | — | — | — | — |
| `headset` | tracked | — | 1 | 3 years | 1 | 2 years | 1 | 2 years |
| `mouse` | untracked | — | 2 | — | 1 | — | 2 | — |
| `keyboard` | untracked | — | 1 | — | 1 | — | 1 | — |
| `external_ssd` | untracked | — | 2 | — | 1 | — | 2 | — |
| `webcam` | untracked | — | 1 | — | — | — | 1 | — |

An item that does not appear in this table has no policy record. An unknown role or unknown employment type has no limits.

Interns do not receive a laptop, a dock, or a webcam. Contractors do not receive a dock. Only a `manager` receives a laptop, and then only if that employment type has a laptop column.

## When a tracked item is still inside its cadence

This map is shared. It does not change per item or employment type. It applies only after the quantity, role, and employment-type checks have passed, and only when the newest unit is newer than the cadence.

| Reason | Result |
|---|---|
| `preference` | Deny. Code `too_soon`. |
| `performance` | Deny. Code `too_soon`. |
| `broken` | Escalate. Code `early_replacement`. |
| `lost` | Escalate. Code `early_replacement`. |
| `new_hire` | Escalate. Code `early_replacement`. |

A `new_hire` reason from someone with tenure of 1 year or more never reaches this map. That request is a deny, code `reason_conflict`, for every item.

## Evaluation

`check_request_eligibility` runs these steps in order and stops at the first result. The steps do not name a specific item. They load the catalog row, then the limit object for that employee's employment type.

1. Employee id is missing from the file. Result `unknown` / `not_found`. Escalate.
2. No catalog row exists for the item. Result `unknown` / `no_rule`. Escalate.
3. The row requires a role the employee does not have, or the employment type has no limit on that row. Result `denied` / `not_allowed`.
4. Quantity is missing or is not an integer of 1 or greater. Result `denied` / `invalid_quantity`.
5. Quantity is greater than that employment type's `max_quantity`. Result `denied` / `over_quantity`.
6. Reason is `new_hire` and `tenure_years` is 1 or more. Result `denied` / `reason_conflict`.
7. The item is untracked. Result `eligible` / `ok`.
8. The item is tracked, and the file has no unit of it, or the newest `issued_on` meets the cadence. Result `eligible` / `ok`.
9. The item is tracked, the newest unit is inside the cadence, and the reason map says deny. Result `denied` / `too_soon`.
10. The item is tracked, the newest unit is inside the cadence, and the reason map says escalate. Result `unknown` / `early_replacement`.

`eligible` is an approval. `denied` is final. `unknown` is not a deny. The agent escalates it with `flag_for_human_review`.

A `new_hire` whose tenure is under 1 year falls through. With no unit on file, step 8 approves the starter issue. With a unit still inside the cadence, step 10 escalates.

## How the agent uses this

The ReAct loop investigates, then accepts the tool result. It does not keep its own copy of the table.

1. `get_employee_info(employee_id)` for role, employment type, tenure, and tracked gear.
2. `get_policy_limits(role)` for the catalog rows that role may receive, each with the three employment-type limit objects.
3. `check_request_eligibility(employee_id, item, reason, quantity)` for the outcome and code. That tool picks the limit for the employee's employment type.
4. On `unknown`, `flag_for_human_review(employee_id, request, reason)` with the code as the reason. On `eligible`, approve. On `denied`, deny.

The draft may mention only facts those tools returned: the role, the employment type, the issued date, the max quantity, the cadence, and the outcome code. Reflection drops any claim that is not in an observation.

## Examples

These rows are cases the tests and the four demos must cover. They are not extra rules. Two requests that hit the same step are the same kind of decision.

| Case | Request | Record | Step | Result |
|---|---|---|---|---|
| First monitors | `E100`, `monitor`, `preference`, `2` | `standard`, `fulltime`, no monitor | 8 | `eligible` / `ok` |
| Monitor refresh | `E100`, `monitor`, `preference`, `1` | `standard`, `fulltime`, monitor `2022-01-15` | 8 | `eligible` / `ok` |
| Dual mouse | `E100`, `mouse`, `broken`, `2` | `standard`, `fulltime` | 7 | `eligible` / `ok` |
| Contractor SSDs | `E400`, `external_ssd`, `preference`, `2` | `standard`, `contractor` | 7 | `eligible` / `ok` |
| Intern headset | `E300`, `headset`, `preference`, `1` | `standard`, `intern`, no headset | 8 | `eligible` / `ok` |
| Starter laptop | `E201`, `laptop`, `new_hire`, `1` | `manager`, `fulltime`, tenure 0, no laptop | 8 | `eligible` / `ok` |
| Too many mice | `E100`, `mouse`, `preference`, `3` | `standard`, `fulltime`, max 2 | 5 | `denied` / `over_quantity` |
| Intern over SSD | `E300`, `external_ssd`, `preference`, `2` | `standard`, `intern`, max 1 | 5 | `denied` / `over_quantity` |
| Second laptop | `E200`, `laptop`, `preference`, `2` | `manager`, `fulltime`, max 1 | 5 | `denied` / `over_quantity` |
| Early laptop | `E200`, `laptop`, `preference`, `1` | `manager`, `fulltime`, laptop `2025-06-01` | 9 | `denied` / `too_soon` |
| Standard wants a laptop | `E100`, `laptop`, `preference`, `1` | `standard`, `fulltime` | 3 | `denied` / `not_allowed` |
| Intern wants a laptop | `E301`, `laptop`, `preference`, `1` | `manager`, `intern` | 3 | `denied` / `not_allowed` |
| Intern wants a dock | `E300`, `dock`, `preference`, `1` | `standard`, `intern` | 3 | `denied` / `not_allowed` |
| Contractor wants a dock | `E400`, `dock`, `preference`, `1` | `standard`, `contractor` | 3 | `denied` / `not_allowed` |
| Intern wants a webcam | `E300`, `webcam`, `preference`, `1` | `standard`, `intern` | 3 | `denied` / `not_allowed` |
| Not a new hire | `E100`, `headset`, `new_hire`, `1` | `standard`, `fulltime`, tenure 4 | 6 | `denied` / `reason_conflict` |
| Bad quantity | `E100`, `keyboard`, `broken`, `0` | `standard`, `fulltime` | 4 | `denied` / `invalid_quantity` |
| Unknown person | `E999`, `mouse`, `broken`, `1` | no employee | 1 | `unknown` / `not_found` |
| Not in the catalog | `E100`, `chair`, `broken`, `1` | `standard`, `fulltime` | 2 | `unknown` / `no_rule` |
| Broken laptop inside 2 years | `E200`, `laptop`, `broken`, `1` | `manager`, `fulltime`, laptop `2025-06-01` | 10 | `unknown` / `early_replacement` |
| Lost monitor inside 3 years | `E100`, `monitor`, `lost`, `1` | `standard`, `fulltime`, monitor `2025-01-01` | 10 | `unknown` / `early_replacement` |

## Demo paths

The assignment run is four of the rows above. The other rows are unit tests.

| Path | Request | Why |
|---|---|---|
| Approve | `E100`, `monitor`, `preference`, `1`, `standard` / `fulltime`, monitor issued `2022-01-15` | Cadence is met |
| Deny | `E200`, `laptop`, `preference`, `1`, `manager` / `fulltime`, laptop issued `2025-06-01` | Preference inside 2 years |
| Escalate | `E999`, `mouse`, `broken`, `1` | Employee is not on file |
| Escalate | `E200`, `laptop`, `broken`, `1`, laptop issued `2025-06-01` | Broken inside the cadence |

A standard employee asking for a laptop, and an intern asking for a laptop or a dock, are denies (`not_allowed`). They are not escalations.

## Tool contracts

`get_employee_info(employee_id)` returns `found: false` when the id is missing. Otherwise it returns `employee_id`, `name`, `role` (`standard` or `manager`), `employment_type` (`fulltime`, `intern`, or `contractor`), `tenure_years`, and `equipment`. Equipment lists tracked items only, each as `item` and `issued_on`.

`get_policy_limits(role)` returns the catalog rows that role may receive: `item`, `kind`, `required_role`, and `limits` keyed by employment type. Each limit is `max_quantity` and `cadence_years` (`null` when untracked). An employment type with a dash in the catalog is omitted from `limits`. An unknown role returns an empty list.

`check_request_eligibility(employee_id, item, reason, quantity)` returns `outcome` (`eligible`, `denied`, or `unknown`) and `code` from the evaluation steps. It uses the employee's role and employment type. It does not take those as arguments.

`flag_for_human_review(employee_id, request, reason)` appends one queue record and returns its id. This is the only write. `request` is the four fields. `reason` is `not_found`, `no_rule`, or `early_replacement`.
