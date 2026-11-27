def convert_rows(lines):
    values = []
    invalid_rows = []
    for line_number, line in enumerate(lines, start=1):
        try:
            values.append(int(line))
        except ValueError:
            invalid_rows.append(line_number)
            break
    return {"values": values, "invalid_rows": invalid_rows}
