from datetime import datetime


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M")
    except ValueError:
        return None


def hours_between(start, end):
    """Return hours (float) between two HH:MM strings, or None if either is missing/invalid."""
    t1, t2 = _parse_time(start), _parse_time(end)
    if not t1 or not t2:
        return None
    delta = (t2 - t1).total_seconds() / 3600
    if delta < 0:
        delta += 24
    return round(delta, 2)


def compute_attendance_hours(check_in, check_out, break_start, break_end):
    total_break = hours_between(break_start, break_end) or 0
    total_work = hours_between(check_in, check_out)
    net_work = None
    if total_work is not None:
        net_work = round(max(total_work - total_break, 0), 2)
    return total_break, net_work


def compute_productivity(actual_output, harvest_target):
    """Percentage of target reached. None if there's no target to measure against."""
    if not harvest_target:
        return None
    return round((actual_output / harvest_target) * 100, 1)


def next_work_status(actual_output, harvest_target, current_status):
    if current_status == "Completed" and actual_output <= 0:
        return "Pending"
    if harvest_target and actual_output >= harvest_target:
        return "Completed"
    if actual_output > 0:
        return "In Progress"
    return "Pending"


def compute_harvest_pay(actual_output, harvest_target, rate_per_kg, commission_percent):
    """kg up to the target are paid at rate_per_kg; kg beyond the target earn a
    commission_percent bonus on top of rate_per_kg. Example: target 25kg, rate 50,
    commission 20%, actual 29kg -> 25kg x 50 + 4kg x 50x1.20 = 1250 + 240 = 1490."""
    if not rate_per_kg or not actual_output:
        return {"base_kg": 0, "base_pay": 0, "bonus_kg": 0, "bonus_pay": 0, "total_pay": 0}

    if harvest_target and actual_output > harvest_target:
        base_kg = harvest_target
        bonus_kg = round(actual_output - harvest_target, 2)
    else:
        base_kg = actual_output
        bonus_kg = 0

    base_pay = round(base_kg * rate_per_kg, 2)
    bonus_rate = rate_per_kg * (1 + (commission_percent or 0) / 100)
    bonus_pay = round(bonus_kg * bonus_rate, 2)

    return {
        "base_kg": base_kg,
        "base_pay": base_pay,
        "bonus_kg": bonus_kg,
        "bonus_pay": bonus_pay,
        "total_pay": round(base_pay + bonus_pay, 2),
    }


# Sri Lanka statutory rates: employee contributes 8% of pay to EPF (deducted from
# their pay); the employer separately contributes 12% EPF + 3% ETF on top — a real
# cost to the estate, but not deducted from the employee.
EPF_EMPLOYEE_PERCENT = 8
EPF_EMPLOYER_PERCENT = 12
ETF_EMPLOYER_PERCENT = 3


def compute_epf_etf(total_pay, applicable):
    if not applicable or not total_pay:
        return {"employee_epf": 0, "employer_epf": 0, "employer_etf": 0}
    return {
        "employee_epf": round(total_pay * EPF_EMPLOYEE_PERCENT / 100, 2),
        "employer_epf": round(total_pay * EPF_EMPLOYER_PERCENT / 100, 2),
        "employer_etf": round(total_pay * ETF_EMPLOYER_PERCENT / 100, 2),
    }
