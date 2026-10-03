# IT equipment requests

The server decides from policy records. Each piece of equipment has its own limits, and those limits differ by employment type. The same evaluation steps run for every item, so adding a headset or an external SSD does not add a new branch. The agent does not reimplement those steps. It calls the MCP tools and follows what they return.

There is no `standard` or `manager` rank. The only classification on an employee is `employment_type`. The lab tool is still named `get_policy_limits(role)`. That argument is the employment type.

The policy clock is fixed at **2026-10-01**. Age is whole years against that day. A monitor issued on 2023-10-01 is 3 years old. One issued on 2023-10-02 is still inside a 3-year cadence.

## Request fields

The employee message supplies only these four fields. Employment type, tenure, and current gear come from `get_employee_info`. They are not request fields.

| Field | Type | Required | Values |
|---|---|---|---|
| `employee_id` | string | yes | Id in the employee file |
| `item` | string | yes | A key in the catalog below, or anything else |
| `reason` | string | yes | `broken`, `lost`, `new_hire`, `preference`, `performance` |
| `quantity` | integer | yes | Whole number, 1 or greater |

A manager name, a needed-by date, or an asset tag in the message is ignored. Mice, keyboards, external SSDs, and webcams are not tracked assets and have no tag.

A reason outside those five values is not a blank the server may ignore. "Personal use" is one of those phrases. It is `unknown` / `ambiguous` and is escalated.

## Employee record

`get_employee_info` returns one classification besides tenure and gear.

| Field | Values | What it controls |
|---|---|---|
| `employment_type` | `fulltime`, `intern`, `contractor` | Which catalog limits apply: max quantity and cadence |

## Catalog

This table is the data, not a decision tree. A dash means that employment type has no limit for the item and the request is denied.

`max_quantity` is the most that employment type may ask for in one request. `cadence_years` applies only to tracked items: another request is clearly allowed when there is no unit on file, or when the newest `issued_on` is at least that many whole years before the policy clock. Untracked items are not stored on the employee and have no cadence.

| Item | Kind | Full-time max | Full-time cadence | Intern max | Intern cadence | Contractor max | Contractor cadence |
|---|---|---|---|---|---|---|---|
| `monitor` | tracked | 2 | 3 years | 1 | 3 years | 1 | 2 years |
| `laptop` | tracked | 1 | 2 years | — | — | 1 | 2 years |
| `dock` | tracked | 1 | 4 years | — | — | — | — |
| `headset` | tracked | 1 | 3 years | 1 | 2 years | 1 | 2 years |
| `mouse` | untracked | 2 | — | 1 | — | 2 | — |
| `keyboard` | untracked | 1 | — | 1 | — | 1 | — |
| `external_ssd` | untracked | 2 | — | 1 | — | 2 | — |
| `webcam` | untracked | 1 | — | — | — | 1 | — |

An item that does not appear in this table has no policy record. An unknown employment type has no limits.

Interns do not receive a laptop, a dock, or a webcam. Contractors do not receive a dock. Full-time employees and contractors may receive a laptop.

## When a tracked item is still inside its cadence

This map is shared. It does not change per item or employment type. It applies only after the quantity, reason, and employment-type checks have passed, and only when the newest unit is newer than the cadence.

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
3. The employment type has no limit on that row. Result `denied` / `not_allowed`.
4. Quantity is missing or is not an integer of 1 or greater. Result `denied` / `invalid_quantity`.
5. Quantity is greater than that employment type's `max_quantity`. Result `denied` / `over_quantity`.
6. Reason is missing or is not one of the five values. Result `unknown` / `ambiguous`. Escalate. This covers a phrase such as "personal use".
7. Reason is `new_hire` and `tenure_years` is 1 or more. Result `denied` / `reason_conflict`.
8. The item is untracked. Result `eligible` / `ok`.
9. The item is tracked, and the file has no unit of it, or the newest `issued_on` meets the cadence. Result `eligible` / `ok`.
10. The item is tracked, the newest unit is inside the cadence, and the reason map says deny. Result `denied` / `too_soon`.
11. The item is tracked, the newest unit is inside the cadence, and the reason map says escalate. Result `unknown` / `early_replacement`.

`eligible` is an approval. `denied` is final. `unknown` is not a deny. The agent escalates it with `flag_for_human_review`.

A `new_hire` whose tenure is under 1 year falls through. With no unit on file, step 9 approves the starter issue. With a unit still inside the cadence, step 11 escalates.

## How the agent uses this

The ReAct loop investigates, then accepts the tool result. It does not keep its own copy of the table.

1. `get_employee_info(employee_id)` for employment type, tenure, and tracked gear.
2. `get_policy_limits(role)` with `role` set to that employment type, for the rows and limits that type may receive.
3. `check_request_eligibility(employee_id, item, reason, quantity)` for the outcome and code.
4. On `unknown`, `flag_for_human_review(employee_id, request, reason)` with the code as the reason. On `eligible`, approve. On `denied`, deny.

Before the call, an item phrase that matches a catalog key is rewritten to that key (`external ssd` becomes `external_ssd`), and a reason phrase that means one of the five values is rewritten to that value (`it broke` becomes `broken`). A phrase that matches none of them, such as play games, is sent unchanged so the server can return `ambiguous`. A repeated review flag with the same arguments ends the loop.

A sentence can name more than one item. Each tool call still checks one item. The loop allows 16 steps. The printed answer then has one line per check: Approved, Denied, or Escalated, plus that check's outcome code. A single decision is printed only when every check agrees, and that decision is only approve, deny, or escalate. If the loop ends before a finish step, those lines are still printed, followed by how many steps ran.

The draft may mention only facts those tools returned: the employment type, the issued date, the max quantity, the cadence, and the outcome code. Reflection drops any claim that is not in an observation. It also replaces the finish decision and outcome code when they disagree with `check_request_eligibility`, and it drops a decision when that tool was never called. The response printed after reflection is the final one.

## Examples

These rows are cases the tests and the four demos must cover. They are not extra rules. Two requests that hit the same step are the same kind of decision.

| Case | Request | Record | Step | Result |
|---|---|---|---|---|
| First monitors | `E100`, `monitor`, `preference`, `2` | `fulltime`, no monitor | 9 | `eligible` / `ok` |
| Monitor refresh | `E100`, `monitor`, `preference`, `1` | `fulltime`, monitor `2022-01-15` | 9 | `eligible` / `ok` |
| Dual mouse | `E100`, `mouse`, `broken`, `2` | `fulltime` | 8 | `eligible` / `ok` |
| Contractor SSDs | `E400`, `external_ssd`, `preference`, `2` | `contractor` | 8 | `eligible` / `ok` |
| Intern headset | `E300`, `headset`, `preference`, `1` | `intern`, no headset | 9 | `eligible` / `ok` |
| Starter laptop | `E201`, `laptop`, `new_hire`, `1` | `fulltime`, tenure 0, no laptop | 9 | `eligible` / `ok` |
| Too many mice | `E100`, `mouse`, `preference`, `3` | `fulltime`, max 2 | 5 | `denied` / `over_quantity` |
| Intern over SSD | `E300`, `external_ssd`, `preference`, `2` | `intern`, max 1 | 5 | `denied` / `over_quantity` |
| Second laptop | `E200`, `laptop`, `preference`, `2` | `fulltime`, max 1 | 5 | `denied` / `over_quantity` |
| Early laptop | `E200`, `laptop`, `preference`, `1` | `fulltime`, laptop `2025-06-01` | 10 | `denied` / `too_soon` |
| Intern wants a laptop | `E300`, `laptop`, `preference`, `1` | `intern` | 3 | `denied` / `not_allowed` |
| Intern wants a dock | `E300`, `dock`, `preference`, `1` | `intern` | 3 | `denied` / `not_allowed` |
| Contractor wants a dock | `E400`, `dock`, `preference`, `1` | `contractor` | 3 | `denied` / `not_allowed` |
| Intern wants a webcam | `E300`, `webcam`, `preference`, `1` | `intern` | 3 | `denied` / `not_allowed` |
| Personal use | `E100`, `laptop`, `personal use`, `1` | `fulltime`, no laptop | 6 | `unknown` / `ambiguous` |
| Not a new hire | `E100`, `headset`, `new_hire`, `1` | `fulltime`, tenure 4 | 7 | `denied` / `reason_conflict` |
| Bad quantity | `E100`, `keyboard`, `broken`, `0` | `fulltime` | 4 | `denied` / `invalid_quantity` |
| Unknown person | `E999`, `mouse`, `broken`, `1` | no employee | 1 | `unknown` / `not_found` |
| Not in the catalog | `E100`, `chair`, `broken`, `1` | `fulltime` | 2 | `unknown` / `no_rule` |
| Broken laptop inside 2 years | `E200`, `laptop`, `broken`, `1` | `fulltime`, laptop `2025-06-01` | 11 | `unknown` / `early_replacement` |
| Lost monitor inside 3 years | `E101`, `monitor`, `lost`, `1` | `fulltime`, monitor `2025-01-01` | 11 | `unknown` / `early_replacement` |

## Demo paths

The assignment run is four of the rows above. The other rows are unit tests.

| Path | Request | Why |
|---|---|---|
| Approve | `E100`, `monitor`, `preference`, `1`, `fulltime`, monitor issued `2022-01-15` | Cadence is met |
| Deny | `E200`, `laptop`, `preference`, `1`, `fulltime`, laptop issued `2025-06-01` | Preference inside 2 years |
| Escalate | `E999`, `mouse`, `broken`, `1` | Employee is not on file |
| Escalate | `E200`, `laptop`, `broken`, `1`, laptop issued `2025-06-01` | Broken inside the cadence |

An intern asking for a laptop or a dock is a deny (`not_allowed`). It is not an escalation. "Personal use" is an escalation (`ambiguous`). It is not one of the four demo paths.

## Tool contracts

`get_employee_info(employee_id)` returns `found: false` when the id is missing. Otherwise it returns `employee_id`, `name`, `employment_type` (`fulltime`, `intern`, or `contractor`), `tenure_years`, and `equipment`. Equipment lists tracked items only, each as `item` and `issued_on`.

`get_policy_limits(role)` takes the employment type as `role`. It returns the catalog rows that type may receive: `item`, `kind`, `max_quantity`, and `cadence_years` (`null` when untracked). Rows with a dash for that type are omitted. An unknown type returns an empty list.

`check_request_eligibility(employee_id, item, reason, quantity)` returns `outcome` (`eligible`, `denied`, or `unknown`) and `code` from the evaluation steps. It reads employment type from the employee file. It does not take it as an argument.

`flag_for_human_review(employee_id, request, reason)` appends one queue record and returns its id. This is the only write. The queue is `data/review_queue.json`, so the next run continues from the last id instead of starting again at `R001`. `request` is the four fields. `reason` is `not_found`, `no_rule`, `early_replacement`, or `ambiguous`.
