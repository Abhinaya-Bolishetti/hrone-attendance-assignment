# REVIEW.md

List of defects found and fixed in the starter `app/main.py`.

| #   | Where (function / line)               | What is wrong                                                                                     | How you'd notice it (test, input, or symptom)                                                                          | How you fixed it                                                                                                                              |
| --- | ------------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `list_employees()` — `GET /employees` | The pagination offset used `page * page_size`, causing page 1 to skip the first set of employees. | Requested page 1 and page 2 with `page_size=2`. The original offset would skip the first two records on page 1.        | Changed the offset to `(page - 1) * page_size`. Verified that page 1 returned EMP0001 and EMP0002, while page 2 returned EMP0003 and EMP0004. |
| 2   | `list_employees()` — `GET /employees` | The total employee count ignored the department filter and counted all employees.                 | Requested employees filtered by department and compared `total` with the number of employees matching that department. | Changed `count_documents({})` to `count_documents(q)`. The department-filtered result still needs verification.                               |

## Items examined and not considered defects

- The `/health` endpoint returned HTTP 200 during the initial setup test. Database availability under failure conditions has not yet been tested.
- The `/favicon.ico` request returned HTTP 404. This is harmless because the assignment does not require a favicon.

# Where What is wrong How you'd notice it How you fixed it

3 list_employees() Results were not explicitly sorted before pagination, so page ordering was not guaranteed. Inspect the query and test pagination with records inserted in a different order. Added .sort("emp_code", 1) before .skip() and .limit().

# Where What is wrong How you'd notice it How you fixed it

4 list_employees() Pagination parameters accepted invalid values. Tested page 0, page size 0, and page size 101. Added Query(default=1, ge=1) and Query(default=20, ge=1, le=100).

### GET /attendance — Pagination and Validation

- **Issue:** The original endpoint loaded all matching attendance records into Python before applying pagination.
- **Fix:** Applied MongoDB `.skip()` and `.limit()` and used `count_documents(q)` for the filtered total.
- **Sorting:** Added deterministic sorting by date descending and employee code ascending.
- **Validation:** Restricted `page` to values greater than or equal to 1 and `page_size` to values between 1 and 100.
- **Verification:** Tested pages 1 and 2 with page size 5. Both returned 5 different records, with `total: 17`.
- **Note:** Invalid parameter values displayed validation errors in Swagger; server-side HTTP 422 should also be verified.

### Additional defects found and fixed

| #   | Where (function / endpoint)               | What is wrong                                                                                                  | How you'd notice it (test, input, or symptom)                                               | How you fixed it                                                                                                       |
| --- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 5   | Attendance timestamp validation           | Invalid timestamp formats or values may be accepted instead of returning HTTP 422.                             | Submit a timestamp in seconds instead of milliseconds, a string, or a floating-point value. | Added strict validation for epoch-millisecond timestamps and rejected invalid inputs.                                  |
| 6   | Attendance calculation — overnight shifts | Attendance date may be assigned incorrectly when a shift crosses midnight.                                     | Test a punch-in before the end time of an overnight shift.                                  | Adjusted attendance-date calculation to associate the punch with the correct shift date.                               |
| 7   | Late-minute calculation                   | Employees exactly 10 minutes late may incorrectly be marked late.                                              | Test punch-in times exactly 10 minutes and 11 minutes after shift start.                    | Applied the rule that lateness is counted only when the delay is strictly greater than 10 minutes.                     |
| 8   | Overtime calculation                      | Overtime may be counted before the minimum qualifying duration or calculated incorrectly for overnight shifts. | Test overtime below 30 minutes, at 30 minutes, and for an overnight shift.                  | Applied the 30-minute eligibility threshold and whole-minute calculation.                                              |
| 9   | Work-hours calculation                    | Work hours may be rounded incorrectly.                                                                         | Test durations that require rounding to two decimal places.                                 | Used decimal half-up rounding to two decimal places.                                                                   |
| 10  | Analytics aggregation endpoints           | Analytics may process records in Python instead of using MongoDB aggregation pipelines.                        | Inspect the endpoint implementation and its database operations.                            | Implemented analytics calculations using MongoDB aggregation pipelines.                                                |
| 11  | Department trend — date coverage          | Dates without attendance records may be omitted from the result.                                               | Request a date range containing weekends or days without attendance records.                | Used MongoDB `$densify` to generate the daily date sequence and fill missing counts.                                   |
| 12  | `/admin/explain/{endpoint}`               | Explain output may not reflect the endpoint's actual query or aggregation.                                     | Compare the explain pipeline and filters with those used by the corresponding endpoint.     | Ensure the explain route builds and explains the same query or aggregation, including relevant filters and pagination. |
