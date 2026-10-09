from fractions import Fraction
import json
import pytest
from prometheus_assistant.exact_math import calculate,fraction,linear,quadratic,direct_expression
from prometheus_assistant.agent import run_agent
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.cli import main

@pytest.mark.parametrize('text,value',[('0.1+0.2','3/10'),('(2+3)*4','20'),('1/3+1/6','1/2'),('2**-3','1/8'),('1e3/4','250'),('-2**2','-4')])
def test_exact_arithmetic(text,value):assert calculate(text)['exact']==value

@pytest.mark.parametrize('text',['1/0','0**-1','2**100000','2**(1/2)','True','1<<100','0xff','[1,2]','a+1','__import__("os")','(1).__class__','sum([1,2])'])
def test_rejects_execution_ambiguous_literals_and_unbounded_work(text):
    with pytest.raises(ValueError):fraction(text)

def test_linear_checks_substitution_and_degenerate_cases():
    assert linear('2','3','11')['roots']==['4']
    assert linear('0','3','3')['solution_kind']=='all real numbers'
    assert linear('0','3','4')['solution_kind']=='no solution'

def test_quadratics_preserve_exact_roots_and_distinguish_symbolic_results():
    assert quadratic('1','-5','6')['roots']==['2','3']
    assert quadratic('1','-4','4')['roots']==['2']
    assert quadratic('1','0','-2')['solution_kind']=='irrational real roots'
    assert quadratic('1','0','1')['solution_kind']=='complex roots'
    assert not quadratic('1','0','1')['substitution_verified']

def test_generated_rational_quadratics_against_independent_roots():
    for r in range(-8,9):
        for s in range(-8,9):
            result=quadratic('1',str(-r-s),str(r*s))
            assert {Fraction(x) for x in result['roots']}=={Fraction(r),Fraction(s)}

def test_pure_arithmetic_routes_without_a_model_and_keeps_evidence(tmp_path):
    class NoModel:
        def chat(self,*args,**kwargs):raise AssertionError('Arithmetic should not need a model')
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        result=run_agent(memory,NoModel(),build_builtin_registry(memory),'Calculate 0.1 plus 0.2')
        assert result.core.reply.startswith('3/10') and result.attempts==0
        assert result.core.evidence[0].worker=='math'
        assert result.core.evidence[0].status=='complete'

def test_no_numeric_routing_for_requests_with_actions_or_units():
    assert direct_expression('Calculate 2+3 and delete my files') is None
    assert direct_expression('Calculate 2 mg plus 3 mg') is None
    assert direct_expression('What is 8 times 7?')=='8 * 7'

def test_cli_does_not_start_a_model(capsys):
    assert main(['math','quadratic','--a','1','--b','-5','--c','6'])==0
    assert json.loads(capsys.readouterr().out)['roots']==['2','3']
