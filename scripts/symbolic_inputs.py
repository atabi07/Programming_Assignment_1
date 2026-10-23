"""Boolean symbol recognition: algebraic expressions and IEC/Unicode gate labels.

No eval(), no third-party dependencies. Only variables A..D and 2..4-input
single-output combinational functions are supported by the Verilog optimizer.
"""
from __future__ import annotations
import re
from pathlib import Path

# IEC boxes: & = AND, >=1 = OR, =1 = exactly one asserted input.
# For two inputs =1 is XOR. For 3+ it differs from odd parity!
_ALIASES = {
    'AND': {'AND', '&', '∧', '·', '*'},
    'OR': {'OR', '|', '∨', '+', '≥1', '>=1'},
    'XOR': {'XOR', '⊕', '^'},
    'EXACTLY1': {'EXACTLY1', '=1'},
    'XNOR': {'XNOR', '⊙', '≡', '↔'},
    'NAND': {'NAND', '⊼', '↑'},
    'NOR': {'NOR', '⊽', '↓'},
    'NOT': {'NOT', '¬', '!', '~'},
    'BUF': {'BUF', 'BUFFER', '1'},
    'CONST0': {'CONST0', 'FALSE'},
    'CONST1': {'CONST1', 'TRUE'},
}
NORMALIZED = {label.upper(): gate for gate, labels in _ALIASES.items() for label in labels}
INVERTED = {'AND':'NAND', 'OR':'NOR', 'XOR':'XNOR', 'XNOR':'XOR',
            'NAND':'AND', 'NOR':'OR', 'BUF':'NOT', 'NOT':'BUF',
            'CONST0':'CONST1', 'CONST1':'CONST0', 'EXACTLY1':'NOTEXACTLY1',
            'NOTEXACTLY1':'EXACTLY1'}

def gate_type(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError('Gate label must be a string')
    value = text.strip().upper()
    if value == 'NOTEXACTLY1': return value
    try: return NORMALIZED[value]
    except KeyError: raise ValueError(f'Unknown logic gate symbol: {text!r}') from None

# Binary operator precedence: NOT > AND/NAND > XOR/XNOR > OR/NOR.
BIN_OPS = {
    '∧': (30, 'AND'), '·': (30, 'AND'), '&': (30, 'AND'), '*': (30, 'AND'),
    '↑': (30, 'NAND'), '⊼': (30, 'NAND'),
    '⊕': (20, 'XOR'), '^': (20, 'XOR'), '⊙': (20, 'XNOR'), '≡': (20, 'XNOR'),
    '↔': (20, 'XNOR'),
    '∨': (10, 'OR'), '|': (10, 'OR'), '+': (10, 'OR'),
    '↓': (10, 'NOR'), '⊽': (10, 'NOR'),
}
PREFIX_NOT = {'¬', '~', '!'}

class Parser:
    def __init__(self, expression: str):
        self.expression = expression
        self.tokens: list[str] = []
        self.index = 0
        i = 0
        while i < len(expression):
            c = expression[i]
            if c.isspace(): i += 1; continue
            if c.isalpha() and c.isascii():
                word = re.match(r'[A-Za-z_][A-Za-z_0-9]*', expression[i:]).group().upper()
                keywords = {'AND':'∧', 'OR':'∨', 'NOT':'¬', 'XOR':'⊕',
                            'XNOR':'⊙', 'NAND':'⊼', 'NOR':'⊽'}
                if word in keywords:
                    self.tokens.append(keywords[word])
                elif all(char in 'ABCD' for char in word):
                    # Ordinary algebraic notation AB'C implies adjacent ANDs.
                    self.tokens.extend(word)
                else:
                    raise ValueError(f'Unsupported name {word!r}; variables must be A, B, C, D')
                i += len(word); continue
            if c in '01()\'':
                self.tokens.append(c); i += 1; continue
            if c in BIN_OPS or c in PREFIX_NOT:
                self.tokens.append(c); i += 1; continue
            raise ValueError(f'Unknown expression symbol {c!r} at position {i+1}')
        if not self.tokens: raise ValueError('Empty Boolean expression')

    def peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> str:
        result = self.peek()
        if result is None: raise ValueError('Unexpected end of expression')
        self.index += 1
        return result

    def parse_atom(self):
        token = self.take()
        if token in PREFIX_NOT:
            node = ('NOT', self.parse_expression(40))
        elif token == '(':
            node = self.parse_expression(0)
            if self.take() != ')': raise ValueError('Expected closing parenthesis')
        elif token in 'ABCD' and len(token) == 1:
            node = ('VAR', token)
        elif token in ('0','1'):
            node = ('CONST', int(token))
        else:
            raise ValueError(f'Expected a variable, constant or group, got {token!r}')
        while self.peek() == "'":
            self.take()
            node = ('NOT', node)
        return node

    def parse_expression(self, minimum: int = 0):
        node = self.parse_atom()
        while True:
            token = self.peek()
            # Adjacent factors such as A'B or (A+B)C imply AND.
            implicit = token is not None and (token in PREFIX_NOT or token == '(' or token in 'ABCD01')
            if token in BIN_OPS:
                precedence, op = BIN_OPS[token]
            elif implicit:
                precedence, op = 30, 'AND'
            else:
                break
            if precedence < minimum: break
            if not implicit: self.take()
            node = (op, node, self.parse_expression(precedence + 1))
        return node

    def parse(self):
        node = self.parse_expression()
        if self.peek() is not None:
            raise ValueError(f'Unexpected symbol {self.peek()!r}')
        return node


def evaluate(node, assignment: dict[str,int]) -> int:
    kind = node[0]
    if kind == 'VAR': return assignment[node[1]]
    if kind == 'CONST': return node[1]
    if kind == 'NOT': return 1 - evaluate(node[1], assignment)
    left = evaluate(node[1], assignment)
    right = evaluate(node[2], assignment)
    if kind == 'AND': return left & right
    if kind == 'OR': return left | right
    if kind == 'XOR': return left ^ right
    if kind == 'NAND': return 1 - (left & right)
    if kind == 'NOR': return 1 - (left | right)
    if kind == 'XNOR': return 1 - (left ^ right)
    raise ValueError(f'Unknown AST node {kind}')


def load_expression(path: Path) -> tuple[int,list[int]]:
    raw = path.read_text(encoding='utf-8-sig')
    lines = [re.sub(r'#.*$|//.*$', '', s).strip() for s in raw.splitlines()]
    lines = [s for s in lines if s]
    count = None
    if lines and re.fullmatch(r'(?i)INPUTS\s*[:=]\s*[234]', lines[0]):
        count = int(re.search('[234]$', lines[0]).group())
        lines.pop(0)
    expression = ' '.join(lines).strip()
    expression = re.sub(r'^[FY]\s*=\s*', '', expression, count=1, flags=re.I)
    ast = Parser(expression).parse()
    def used_variables(node):
        if node[0] == 'VAR': return {node[1]}
        if node[0] == 'CONST': return set()
        if node[0] == 'NOT': return used_variables(node[1])
        return used_variables(node[1]) | used_variables(node[2])
    highest = max(('ABCD'.index(a)+1 for a in used_variables(ast)), default=0)
    n = count or max(2, highest)
    if highest > n: raise ValueError(f'Expression refers to a variable beyond INPUTS={n}')
    table = []
    for m in range(1 << n):
        assignment = {a: (m >> (n - i - 1)) & 1 for i,a in enumerate('ABCD'[:n])}
        table.append(evaluate(ast, assignment))
    return n, table
