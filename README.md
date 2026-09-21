# Universal Commerce Protocol - Meeting Minutes

This repository serves as the official, transparent, and version-controlled record of the Universal Commerce Protocol (UCP) Governing Council (GC), Domain Tech Council (DTC), and Domain Working Group (DWG) decisions and discussions.

## Objective

To maintain a clear public record of governance operations and decisions, modeling industry standards for open source transparency.

Per [`GOVERNANCE.md`](https://github.com/Universal-Commerce-Protocol/.github/blob/main/GOVERNANCE.md#communication), DTC and DWG minutes should be published here within one week of the meeting concluding.

## Repository Structure

Minutes are organized by governance body, then chronologically:

- **`gc/`**: Governing Council minutes.
  - **`template.md`**: The standard markdown template for creating new GC meeting notes.
  - **`YYYY/GC-YYYY-MM-DD.md`**: Individual meeting files (e.g., `gc/2026/GC-2026-06-17.md`).
- **`tc/`**: Domain Tech Council minutes, one directory per council.
  - **`template.md`**: The standard markdown template for DTC meeting notes.
  - **`dwg-template.md`**: The standard markdown template for DWG meeting notes.
  - **`<domain>/`**: One directory per Domain Tech Council — `shopping/`, `food/`, `lodging/`, `payments/`.
    - **`README.md`**: Council charter links, membership announcement, and its DWG index.
    - **`YYYY/YYYY-MM-DD.md`**: Individual meeting files (e.g., `tc/shopping/2026/2026-08-07.md`).
    - **`dwg/<working-group>/`**: Minutes for a Domain Working Group sponsored by that council.
      - **`README.md`**: DWG charter link, chairs, and target completion date.
      - **`YYYY/YYYY-MM-DD.md`**: Individual DWG meeting files.

### Domain Tech Councils

| Council     | Directory                      | Charter                                                                                                                           |
| ----------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| Shopping TC | [`tc/shopping/`](tc/shopping/) | [GOVERNANCE.md](https://github.com/Universal-Commerce-Protocol/.github/blob/main/GOVERNANCE.md#shopping-tech-council-shopping-tc) |
| Food TC     | [`tc/food/`](tc/food/)         | [GOVERNANCE.md](https://github.com/Universal-Commerce-Protocol/.github/blob/main/GOVERNANCE.md#food-tech-council-food-tc)         |
| Lodging TC  | [`tc/lodging/`](tc/lodging/)   | [GOVERNANCE.md](https://github.com/Universal-Commerce-Protocol/.github/blob/main/GOVERNANCE.md#lodging-tech-council-lodging-tc)   |
| Payments TC | [`tc/payments/`](tc/payments/) | [GOVERNANCE.md](https://github.com/Universal-Commerce-Protocol/.github/blob/main/GOVERNANCE.md#payments-tech-council-payments-tc) |

### Domain Working Groups

[Domain Working Groups](https://github.com/Universal-Commerce-Protocol/.github/blob/main/GOVERNANCE.md#domain-working-groups-dwg) are chartered for a finite scope and are subject to the oversight of a sponsoring DTC. Their minutes are therefore nested under that council's directory, so DTC ownership applies to DWG artifacts by default.

When a DWG is chartered, create `tc/<sponsoring-domain>/dwg/<working-group>/` with a `README.md` recording the charter issue, chairs/co-chairs, and target completion date, then add it to that council's DWG index. When a DWG concludes, mark it as concluded in its `README.md` — its minutes remain in place as a permanent record.

## Adding Minutes

1. Copy the relevant template (`tc/template.md`, `tc/dwg-template.md`, or `gc/template.md`).
2. Save it to the correct directory using the `YYYY-MM-DD.md` naming convention, creating the year directory if needed.
3. Open a pull request. Approval requirements are enforced per-path; see [`meeting-minutes-rules.yml`](https://github.com/Universal-Commerce-Protocol/.github/blob/main/org-tools/governance/rules/meeting-minutes-rules.yml).

## History

Prior to 2026-09, UCP had a single Tech Council and its minutes lived directly in `tc/YYYY/`. That council is now the Shopping Tech Council, and those minutes have moved to `tc/shopping/YYYY/` unchanged.
