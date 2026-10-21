"""Small, bounded exact arithmetic tools. No eval, exec, imports or file access."""
import ast
from decimal import Decimal, localcontext
from fractions import Fraction
from math import isqrt
import re

MAX_BITS=4096

def _bounded(value):
    if max(value.numerator.bit_length(),value.denominator.bit_length())>MAX_BITS:
        raise ValueError('Calculation exceeds the bounded exact-number size.')
    return value

def fraction(expression):
    if not isinstance(expression,str) or not expression.strip() or len(expression)>512:
        raise ValueError('Use an arithmetic expression of 1–512 characters.')
    expression=expression.strip()
    try:tree=ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError) as error:raise ValueError('Invalid arithmetic expression.') from error
    if sum(1 for _ in ast.walk(tree))>96:raise ValueError('Expression is too complex.')
    def visit(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            token=ast.get_source_segment(expression,node)
            if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?',token):
                raise ValueError('Only decimal number literals are accepted.')
            return _bounded(Fraction(token))
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=visit(node.operand);return value if isinstance(node.op,ast.UAdd) else -value
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow)):
            a=visit(node.left);b=visit(node.right)
            try:
                if isinstance(node.op,ast.Add):value=a+b
                elif isinstance(node.op,ast.Sub):value=a-b
                elif isinstance(node.op,ast.Mult):value=a*b
                elif isinstance(node.op,ast.Div):value=a/b
                else:
                    if b.denominator!=1 or abs(b)>32:raise ValueError('Use an integer exponent between -32 and 32.')
                    value=a**int(b)
            except ZeroDivisionError as error:raise ValueError('Division by zero is undefined.') from error
            return _bounded(value)
        raise ValueError('Only numbers, parentheses, +, -, *, / and bounded ** powers are supported.')
    return visit(tree.body)

def calculate(expression):
    value=fraction(expression)
    with localcontext() as context:
        context.prec=25;decimal=str(Decimal(value.numerator)/Decimal(value.denominator))
    return {'expression':expression,'exact':str(value),'decimal_approximation':decimal,
            'method':'bounded rational arithmetic','model_used':False}

def linear(a,b,c):
    a,b,c=map(fraction,(a,b,c))
    if not a:return {'equation':'a*x+b=c','solution_kind':'all real numbers' if b==c else 'no solution','model_used':False}
    root=(c-b)/a
    assert a*root+b==c
    return {'equation':'a*x+b=c','solution_kind':'one rational root','roots':[str(root)],
            'substitution_verified':True,'method':'Subtract b, then divide by a.','model_used':False}

def quadratic(a,b,c):
    a,b,c=map(fraction,(a,b,c))
    if not a:raise ValueError('For a quadratic equation a must be nonzero; use the linear solver otherwise.')
    discriminant=b*b-4*a*c
    common={'equation':'a*x**2+b*x+c=0','discriminant':str(discriminant),'model_used':False}
    if discriminant>=0:
        n=isqrt(discriminant.numerator);d=isqrt(discriminant.denominator)
        if n*n==discriminant.numerator and d*d==discriminant.denominator:
            radical=Fraction(n,d);roots=sorted(set(((-b-radical)/(2*a),(-b+radical)/(2*a))))
            assert all(a*x*x+b*x+c==0 for x in roots)
            return {**common,'solution_kind':'rational roots','roots':[str(x) for x in roots],
                    'substitution_verified':True,'method':'Quadratic formula with exact substitution checks.'}
    imaginary=discriminant<0
    radical=('i*' if imaginary else '')+'sqrt('+str(abs(discriminant))+')'
    return {**common,'solution_kind':'complex roots' if imaginary else 'irrational real roots',
            'roots':[f'({-b} - {radical})/({2*a})',f'({-b} + {radical})/({2*a})'],
            'substitution_verified':False,'method':'Exact symbolic quadratic formula; no general symbolic proof engine was run.'}

def direct_expression(prompt):
    match=re.fullmatch(r'(?is)\s*(?:what is|calculate|compute)\s+(.+?)\s*\??(?:\s+answer with the number and a one-sentence explanation\.)?\s*',prompt)
    if not match:return None
    expression=match.group(1).strip()
    for word,operator in [('multiplied by','*'),('divided by','/'),('times','*'),('plus','+'),('minus','-'),('x','*')]:
        expression=re.sub(r'\b'+word+r'\b',operator,expression,flags=re.I)
    if not re.fullmatch(r'[0-9eE+*/().\s-]{1,512}',expression):return None
    return expression
