#!/usr/bin/env python3
"""MiniSQLite - a tiny educational SQL database that runs in the terminal.

Data is stored in database.json (next to where you run the program).
Run:  python3 minisqlite.py
"""

import json
import os
import re
import sys

DB_FILE = "database.json"
TYPES = ("INTEGER", "TEXT")

HELP_TEXT = """Commands:
  .help     Show this help
  .tables   List all tables
  .exit     Save and quit

SQL (every command must end with ;):
  CREATE TABLE students (id INTEGER, name TEXT, age INTEGER);
  INSERT INTO students VALUES (1, 'Ani', 20);
  SELECT * FROM students;
  SELECT * FROM students WHERE id = 1;
  UPDATE students SET age = 21 WHERE id = 1;
  DELETE FROM students WHERE id = 2;

Notes: TEXT values use single quotes ('Ani'). WHERE supports one column with =."""


class SQLError(Exception):
    """Any user mistake. We print it and keep the program running."""


# ---------------------------------------------------------------- storage

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"Warning: could not read {DB_FILE}. Starting with an empty database.")
        return {}


def save_db(db):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
    except OSError as e:
        raise SQLError(f"Could not save {DB_FILE}: {e}")


# ---------------------------------------------------------------- tokenizer

TOKEN_RE = re.compile(
    r"\s*(?:('[^']*')|(-?\d+)|([A-Za-z_][A-Za-z0-9_]*)|([(),=*;])|(\S))"
)


def tokenize(line):
    """Turn text into a list of (kind, value). Kinds: STR, INT, ID, SYM."""
    tokens = []
    for m in TOKEN_RE.finditer(line.strip()):
        text, num, ident, sym, bad = m.groups()
        if text is not None:
            tokens.append(("STR", text[1:-1]))
        elif num is not None:
            tokens.append(("INT", int(num)))
        elif ident is not None:
            tokens.append(("ID", ident))
        elif sym is not None:
            tokens.append(("SYM", sym))
        else:
            if bad == "'":
                raise SQLError("Syntax error: text value is missing a closing quote (').")
            raise SQLError(f"Syntax error: unexpected character '{bad}'.")
    return tokens


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def next(self):
        tok = self.peek()
        if tok[0] is None:
            raise SQLError("Syntax error: command ended unexpectedly.")
        self.pos += 1
        return tok

    def is_keyword(self, word):
        kind, value = self.peek()
        return kind == "ID" and value.upper() == word

    def expect_keyword(self, word):
        kind, value = self.next()
        if kind != "ID" or value.upper() != word:
            raise SQLError(f"Syntax error: expected {word}, got '{value}'.")

    def expect_symbol(self, sym):
        kind, value = self.next()
        if kind != "SYM" or value != sym:
            raise SQLError(f"Syntax error: expected '{sym}', got '{value}'.")

    def accept_symbol(self, sym):
        kind, value = self.peek()
        if kind == "SYM" and value == sym:
            self.pos += 1
            return True
        return False

    def expect_ident(self, what):
        kind, value = self.next()
        if kind != "ID":
            raise SQLError(f"Syntax error: expected {what}, got '{value}'.")
        return value

    def parse_value(self):
        kind, value = self.next()
        if kind in ("STR", "INT"):
            return value
        raise SQLError(f"Syntax error: expected a value (like 20 or 'Ani'), got '{value}'.")

    def expect_end(self):
        if self.pos != len(self.tokens):
            raise SQLError(f"Syntax error: unexpected '{self.tokens[self.pos][1]}'.")


# ---------------------------------------------------------------- helpers

def find_table(db, name):
    for key in db:
        if key.lower() == name.lower():
            return key
    raise SQLError(f"Table '{name}' does not exist.")


def find_column(table, name):
    for i, col in enumerate(table["columns"]):
        if col["name"].lower() == name.lower():
            return i
    raise SQLError(f"Column '{name}' does not exist.")


def check_type(col, value):
    if col["type"] == "INTEGER" and not isinstance(value, int):
        raise SQLError(f"Type mismatch: column '{col['name']}' is INTEGER, but got text '{value}'.")
    if col["type"] == "TEXT" and not isinstance(value, str):
        raise SQLError(f"Type mismatch: column '{col['name']}' is TEXT, but got number {value}. Use quotes: '{value}'.")


def parse_where(p, table):
    """Parse: WHERE <col> = <val>. Returns (column_index, value)."""
    p.expect_keyword("WHERE")
    col_name = p.expect_ident("a column name")
    idx = find_column(table, col_name)
    p.expect_symbol("=")
    value = p.parse_value()
    check_type(table["columns"][idx], value)
    return idx, value


def print_table(columns, rows):
    headers = [c["name"] for c in columns]
    text_rows = [[str(v) for v in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in text_rows]) for i, h in enumerate(headers)]

    def line(cells):
        return " | ".join(c.ljust(w) for c, w in zip(cells, widths))

    print(line(headers))
    print("-+-".join("-" * w for w in widths))
    for r in text_rows:
        print(line(r))
    print(f"({len(rows)} row(s))")


# ---------------------------------------------------------------- commands
# Each function returns True if the database changed (so we save it).

def do_create(p, db):
    p.expect_keyword("TABLE")
    name = p.expect_ident("a table name")
    p.expect_symbol("(")
    columns = []
    while True:
        col_name = p.expect_ident("a column name")
        col_type = p.expect_ident("a column type").upper()
        if col_type not in TYPES:
            raise SQLError(f"Unknown type '{col_type}'. Use INTEGER or TEXT.")
        if any(c["name"].lower() == col_name.lower() for c in columns):
            raise SQLError(f"Duplicate column name '{col_name}'.")
        columns.append({"name": col_name, "type": col_type})
        if p.accept_symbol(","):
            continue
        p.expect_symbol(")")
        break
    p.expect_end()
    if any(k.lower() == name.lower() for k in db):
        raise SQLError(f"Table '{name}' already exists.")
    db[name] = {"columns": columns, "rows": []}
    print(f"Table '{name}' created.")
    return True


def do_insert(p, db):
    p.expect_keyword("INTO")
    key = find_table(db, p.expect_ident("a table name"))
    p.expect_keyword("VALUES")
    p.expect_symbol("(")
    values = [p.parse_value()]
    while p.accept_symbol(","):
        values.append(p.parse_value())
    p.expect_symbol(")")
    p.expect_end()
    table = db[key]
    if len(values) != len(table["columns"]):
        raise SQLError(
            f"Table '{key}' has {len(table['columns'])} column(s), but {len(values)} value(s) were given."
        )
    for col, val in zip(table["columns"], values):
        check_type(col, val)
    table["rows"].append(values)
    print("1 row inserted.")
    return True


def do_select(p, db):
    p.expect_symbol("*")
    p.expect_keyword("FROM")
    key = find_table(db, p.expect_ident("a table name"))
    table = db[key]
    rows = table["rows"]
    if p.is_keyword("WHERE"):
        idx, value = parse_where(p, table)
        rows = [r for r in rows if r[idx] == value]
    p.expect_end()
    print_table(table["columns"], rows)
    return False


def do_update(p, db):
    key = find_table(db, p.expect_ident("a table name"))
    table = db[key]
    p.expect_keyword("SET")
    set_idx = find_column(table, p.expect_ident("a column name"))
    p.expect_symbol("=")
    new_value = p.parse_value()
    check_type(table["columns"][set_idx], new_value)
    w_idx, w_value = parse_where(p, table)
    p.expect_end()
    count = 0
    for row in table["rows"]:
        if row[w_idx] == w_value:
            row[set_idx] = new_value
            count += 1
    print(f"{count} row(s) updated.")
    return count > 0


def do_delete(p, db):
    p.expect_keyword("FROM")
    key = find_table(db, p.expect_ident("a table name"))
    table = db[key]
    w_idx, w_value = parse_where(p, table)
    p.expect_end()
    before = len(table["rows"])
    table["rows"] = [r for r in table["rows"] if r[w_idx] != w_value]
    count = before - len(table["rows"])
    print(f"{count} row(s) deleted.")
    return count > 0


COMMANDS = {
    "CREATE": do_create,
    "INSERT": do_insert,
    "SELECT": do_select,
    "UPDATE": do_update,
    "DELETE": do_delete,
}


def run_sql(line, db):
    tokens = tokenize(line)
    if not tokens:
        return False
    if tokens[-1] != ("SYM", ";"):
        raise SQLError("Syntax error: command must end with ';'.")
    p = Parser(tokens[:-1])
    kind, word = p.next()
    handler = COMMANDS.get(word.upper()) if kind == "ID" else None
    if handler is None:
        raise SQLError(f"Unknown command '{word}'. Type .help for the list of commands.")
    return handler(p, db)


def run_dot_command(line, db):
    """Returns False if the program should exit."""
    cmd = line.strip().lower()
    if cmd == ".help":
        print(HELP_TEXT)
    elif cmd == ".tables":
        if db:
            for name in db:
                print(name)
        else:
            print("(no tables)")
    elif cmd == ".exit":
        return False
    else:
        print(f"Unknown command '{line.strip()}'. Type .help for the list of commands.")
    return True


# ---------------------------------------------------------------- main loop

def main():
    db = load_db()
    print("MiniSQLite. Type .help for help, .exit to quit.")
    while True:
        try:
            line = input("minisqlite> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        try:
            if line.strip().startswith("."):
                if not run_dot_command(line, db):
                    break
            elif run_sql(line, db):
                save_db(db)
        except SQLError as e:
            print(f"Error: {e}")
        except Exception as e:  # last safety net: never crash
            print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
    sys.exit(0)
