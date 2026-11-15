"""Finite OOXML formula evaluator. Formula values are never taken from caches."""
import ast
import io
import operator
import re
import xml.etree.ElementTree as ET
import zipfile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read(raw):
    pages, roots = {}, {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for index, sheet in enumerate(workbook.findall("s:sheets/s:sheet", NS), 1):
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            roots[sheet.attrib["name"]] = root
            cells = {}
            for c in root.findall(".//s:c", NS):
                v, f, t = c.find("s:v", NS), c.find("s:f", NS), c.find("s:is/s:t", NS)
                value = (v.text if c.attrib.get("t") == "str" else float(v.text)) if v is not None else (t.text if t is not None else "")
                cells[c.attrib["r"]] = {"value": value, "formula": f.text if f is not None else None,
                                          "style": c.attrib["s"]}
            pages[sheet.attrib["name"]] = cells
    return pages, roots


def evaluator(pages, edits=None):
    memo, edits = {}, edits or {}

    def cell(sheet, address):
        address = address.replace("$", "")
        if (sheet, address) in edits:
            return edits[sheet, address]
        if (sheet, address) in memo:
            return memo[sheet, address]
        record = pages[sheet].get(address, {"formula": None, "value": 0})
        if record["formula"] is None:
            return record["value"]
        constants, references = [], []

        def string(match):
            constants.append(match[0][1:-1].replace('""', '"'))
            return f"literal_{len(constants) - 1}"

        formula = re.sub(r'"(?:[^"]|"")*"', string, record["formula"])

        def ref(match):
            name = match[1] or sheet
            first, last = match[2].replace("$", ""), match[3]
            references.append((name, first, last.replace("$", "") if last else None))
            return f"ref_{len(references) - 1}"

        formula = re.sub(r"(?<![A-Za-z0-9_])(?:'([^']+)'!)?(\$?[A-Z]+\$?\d+)(?::(\$?[A-Z]+\$?\d+))?", ref, formula)
        formula = formula.replace("<>", "!=").replace("^", "**")
        formula = re.sub(r"(?<![<>=!])=(?!=)", "==", formula)
        ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Lt: operator.lt,
               ast.Gt: operator.gt, ast.LtE: operator.le, ast.GtE: operator.ge,
               ast.Eq: operator.eq, ast.NotEq: operator.ne}

        def visit(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                if node.id.startswith("literal_"):
                    return constants[int(node.id.rsplit("_", 1)[1])]
                if node.id.startswith("ref_"):
                    name, first, last = references[int(node.id.rsplit("_", 1)[1])]
                    if last is None:
                        return cell(name, first)
                    start, end = re.fullmatch(r"([A-Z]+)(\d+)", first), re.fullmatch(r"([A-Z]+)(\d+)", last)
                    assert start[1] == end[1]
                    return [cell(name, start[1] + str(i)) for i in range(int(start[2]), int(end[2]) + 1)]
                return {"TRUE": True, "FALSE": False}[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                return -visit(node.operand)
            if isinstance(node, ast.BinOp):
                return ops[type(node.op)](visit(node.left), visit(node.right))
            if isinstance(node, ast.Compare):
                assert len(node.ops) == 1
                return ops[type(node.ops[0])](visit(node.left), visit(node.comparators[0]))
            if isinstance(node, ast.Call):
                function = node.func.id
                if function == "IF":
                    return visit(node.args[1] if visit(node.args[0]) else node.args[2])
                args = [visit(a) for a in node.args]
                if function == "SUM":
                    return sum(v for arg in args for v in (arg if isinstance(arg, list) else [arg]) if type(v) in (int, float))
                if function == "SUMIFS":
                    assert len(args) == 3 and type(args[2]) in (int, float), "Only exact numeric aggregation is accepted"
                    return sum(amount for amount, key in zip(args[0], args[1], strict=True)
                               if type(key) in (int, float) and key == args[2])
                return {"OR": lambda *a: any(a), "AND": lambda *a: all(a), "NOT": operator.not_,
                    "EXACT": lambda a, b: str(a) == str(b), "ISTEXT": lambda a: isinstance(a, str),
                    "ISNUMBER": lambda a: type(a) in (int, float), "MIN": min, "MAX": max,
                    "MOD": operator.mod, "ABS": abs}[function](*args)
            raise AssertionError(ast.dump(node))

        value = visit(ast.parse(formula, mode="eval").body)
        memo[sheet, address] = value
        return value

    return cell
