"""Source-owned, typed mathematical vocabulary for NanoJev game extraction.

All definitions (including not-yet-executable ones) are stored by the smoke. A
catalog-only definition is a mathematical specification, NOT a gold-label oracle.
Synthetic game behavior is explicitly distinguished from game-theory theorems.
No database or model weights are committed as source of truth.
"""
from __future__ import annotations

from fractions import Fraction
import math
from typing import Any

VERSION = 'nanojev.game-math-registry.v1'

# id, family, formula, input types, output type, dependencies, status, meaning,
# assumptions. Each executable entry MUST have >=1 oracle vector below.
# Extended research backlog contains definitions, but intentionally no executable
# labels until domain assumptions and independent checks have been implemented.
_ROWS = [
 ('ADD','arithmetic','x+y','number,number','number','', 'executable','Sum two numeric values',''),
 ('SUBTRACT','arithmetic','x-y','number,number','number','', 'executable','Subtract the second number from the first',''),
 ('MULTIPLY','arithmetic','x*y','number,number','number','', 'executable','Product',''),
 ('DIVIDE','arithmetic','x/y','number,number','number','', 'executable','Quotient','y != 0'),
 ('REVERSE_SUBTRACT','arithmetic','y-x','number,number','number','SUBTRACT','executable','Reverse subtraction',''),
 ('ABS_DIFFERENCE','arithmetic','abs(x-y)','number,number','number','SUBTRACT','executable','Absolute magnitude of difference',''),
 ('AVERAGE','arithmetic','(x+y)/2','number,number','number','MIDPOINT','executable','Binary average; alias of midpoint',''),
 ('ABS_X_GE_Y','comparison','1[abs(x)>=abs(y)]','number,number','binary','', 'executable','Absolute-value threshold predicate',''),
 ('IF_X_POS_Y_ELSE_X','conditional','y if x>0 else x','number,number','number','GREATER_THAN','executable','Select y only when x is positive',''),
 ('TWICE_X_MINUS_Y','arithmetic','2*x-y','number,number','number','MULTIPLY,SUBTRACT','executable','Twice first operand minus second',''),
 ('DOUBLE_SUM','arithmetic','2*(x+y)','number,number','number','MULTIPLY,ADD','executable','Double the sum',''),
 ('SQUARE_SUM','arithmetic','(x+y)^2','number,number','number','MULTIPLY,ADD','executable','Square the sum',''),
 ('LOWER_BOUND','interval','interval[0]','interval','number','','executable','Lower endpoint of an ordered numeric interval','two endpoints, strictly increasing'),
 ('MIDPOINT','arithmetic','(x+y)/2','number,number','number','ADD,DIVIDE','executable','Arithmetic midpoint (equivalent to binary average)',''),
 ('MIN','comparison','min(x,y)','number,number','number','', 'executable','Smaller operand',''),
 ('MAX','comparison','max(x,y)','number,number','number','', 'executable','Larger operand',''),
 ('GREATER_THAN','comparison','1[x>y]','number,number','binary','', 'executable','Strict greater-than indicator',''),
 ('CLAMP','comparison','min(max(x,lo),hi)','number,number,number','number','MIN,MAX','executable','Clamp to closed interval','lo<=hi'),
 ('ARGMAX','optimization','min(argmax_i values[i])','number_list','index','MAX','executable','Index maximizing values; first index breaks ties','nonempty finite list'),
 ('EXPECTATION_DISCRETE','probability','sum_i p_i*x_i','number_list,probability_list','number','MULTIPLY,ADD','executable','Expectation of finite distribution','same-length nonempty lists, p>=0 and sum p=1'),
 ('CDF_UNIFORM','probability','clip((x-lo)/(hi-lo),0,1)','number,number,number','probability','DIVIDE,CLAMP','executable','CDF for a continuous uniform random variable','lo<hi'),
 ('BAYES_UPDATE_BINARY','inference','(prior*l_true)/(prior*l_true+(1-prior)*l_false)','probability,probability,probability','probability','ADD,MULTIPLY,DIVIDE','executable','Posterior probability of true hypothesis','evidence probability strictly positive'),
 ('EXPECTED_UTILITY','utility','sum_i p_i*u_i','number_list,probability_list','number','EXPECTATION_DISCRETE','executable','Expected utility for specified outcomes','probabilities normalized'),
 ('BEST_RESPONSE','strategy','first argmax_i U_i','number_list','index','ARGMAX','executable','Maximizing discrete action index','opponent strategy fixed; first tie'),
 ('PURE_NASH','equilibrium','{(r,c): u_0(r,c)>=u_0(r2,c) and u_1(r,c)>=u_1(r,c2)}','matrix_2_player','profiles','BEST_RESPONSE','executable','Pure strategy Nash profiles for finite two-player payoff matrix','matrix rectangular and nonempty'),
 ('DISCOUNTED_SUM','dynamic','sum_t gamma^t*r_t','number_list,probability','number','ADD,MULTIPLY','executable','Finite discounted return','0<=gamma<=1'),
 ('SECOND_PRICE_UTILITY','auction','value-clearing_price','number,number','number','SUBTRACT','executable','Winner surplus in second-price auction','the bidder won; losing utility handled separately'),
 ('WIN_PROB_UNIFORM','auction','CDF_UNIFORM(bid,lo,hi)','number,number,number','probability','CDF_UNIFORM','executable','Win probability in two-player first-price auction','independent continuous uniform rival bid; ties probability zero'),
 ('FIRST_PRICE_EXPECTED_SURPLUS','auction','(value-bid)*WIN_PROB_UNIFORM(bid,lo,hi)','number,number,number,number','number','SUBTRACT,MULTIPLY,WIN_PROB_UNIFORM','executable','Expected monetary surplus of a first-price bid','risk-neutral private-value bidder; independent uniform rival bid'),
 ('OPTIMAL_BID_UNIFORM','auction','argmax_{0<=b<=value} FIRST_PRICE_EXPECTED_SURPLUS(value,b,lo,hi)','number,number,number','number','FIRST_PRICE_EXPECTED_SURPLUS,ARGMAX','executable','Closed-form best response to continuous uniform rival bids','risk neutral, 0<=bid<=value, lo>=0, hi>lo, choose lowest bid for equal surplus'),
 ('LOW_PAYOFF','qualitative','0.5*x','number','number','MULTIPLY','executable','Low payoff modifier','synthetic payoff rubric'),
 ('MEDIUM_PAYOFF','qualitative','1.0*x','number','number','MULTIPLY','executable','Medium payoff modifier','synthetic payoff rubric'),
 ('HIGH_PAYOFF','qualitative','1.5*x','number','number','MULTIPLY','executable','High payoff modifier','synthetic payoff rubric'),
 ('LOW_INTELLIGENCE','qualitative','[0.5*x,1.5*x]','number','interval','MULTIPLY','executable','Low-confidence interval around reported resources','synthetic uniform error model, not empirically calibrated'),
 ('MEDIUM_INTELLIGENCE','qualitative','[0.75*x,1.25*x]','number','interval','MULTIPLY','executable','Medium-confidence interval around reported resources','synthetic uniform error model, not empirically calibrated'),
 ('HIGH_INTELLIGENCE','qualitative','[0.9*x,1.1*x]','number','interval','MULTIPLY','executable','High-confidence interval around reported resources','synthetic uniform error model, not empirically calibrated'),
 ('LOW_NEED','qualitative','0.5*x','number','number','MULTIPLY','executable','Illustrative fraction of rival resources they will bid','synthetic opponent policy, not general psychology'),
 ('MEDIUM_NEED','qualitative','0.75*x','number','number','MULTIPLY','executable','Illustrative fraction of rival resources they will bid','synthetic opponent policy, not general psychology'),
 ('HIGH_NEED','qualitative','1.0*x','number','number','MULTIPLY','executable','Illustrative fraction of rival resources they will bid','synthetic opponent policy, not general psychology'),
 ('OWN_MEDIUM_NEED','qualitative','1.0*x','number','number','MULTIPLY','executable','Medium own need uses monetary-surplus objective without win bonus','synthetic auction objective; high/low own need not specified'),
 ('HAWK_VS_DOVE','payoff','V','number','number','', 'executable','Payoff from choosing Hawk versus Dove','canonical symmetric Hawk-Dove contest'),
 ('DOVE_VS_DOVE','payoff','V/2','number','number','DIVIDE','executable','Payoff from Dove against Dove','canonical symmetric Hawk-Dove contest'),
 ('LOGIT_RESPONSE','behavior','exp(lambda*u_i)/sum_j exp(lambda*u_j)','number_list,number','probability_list','', 'executable','Quantal-response softmax probabilities','finite lambda>=0; model of bounded-rational stochastic choice'),
 ('MIXED_NASH','equilibrium','sigma_i in Delta(A_i), sigma_i maximizes expected payoff','payoff_tensor','strategy_profile','EXPECTED_UTILITY,BEST_RESPONSE','catalog_only','Mixed-strategy equilibrium solution','finite game; numerical solver not implemented'),
 ('BAYESIAN_NASH','equilibrium','sigma_i(theta_i) maximizes E[u_i | theta_i]','type_game','strategy_profile','BAYES_UPDATE_BINARY,EXPECTED_UTILITY,BEST_RESPONSE','catalog_only','Bayesian equilibrium with private types','common prior and type/payoff model required'),
 ('OPPONENT_TYPE_PRIOR','inference','P(theta)','type_space','distribution','', 'catalog_only','Prior over privately observed opponent types','type-space and prior parameters required'),
 ('SIGNAL_LIKELIHOOD','inference','P(e | theta)','type,signal','probability','', 'catalog_only','Evidence model for opponent type','signal model required'),
 ('BELIEF_UPDATE','inference','P(theta|e) proportional P(e|theta)*P(theta)','distribution,likelihood','distribution','', 'catalog_only','General categorical Bayesian filter','likelihood and prior normalization required'),
 ('PREDICT_ACTION','behavior','P(a|e)=sum_theta P(a|theta,e)P(theta|e)','strategy_model,posterior','distribution','BELIEF_UPDATE','catalog_only','Posterior predictive opponent action distribution','behavior policy is frozen by game'),
 ('LEVEL_K_RESPONSE','behavior','BR_i(sigma_-i^(k-1))','level,base_strategy,payoffs','strategy','BEST_RESPONSE','catalog_only','Iterated best response to lower-level reasoner','base level-0 policy and tie rules required'),
 ('ROBUST_ARGMAX','risk','argmax_a min_{P in ambiguity_set} E_P[U(a)]','ambiguity_set,utilities','action','ARGMAX,EXPECTED_UTILITY','catalog_only','Maximin expected utility under model ambiguity','nonempty admissible distributions'),
 ('CVAR','risk','E[loss | tail_alpha(loss)]','loss_distribution,probability','number','EXPECTATION_DISCRETE','catalog_only','Conditional value at risk with explicit tail convention','loss orientation and alpha must be declared'),
 ('BELLMAN_BACKUP','dynamic','Q(s,a)=r(s,a)+gamma E[V(s_next)]','transitions,rewards,value','value','EXPECTED_UTILITY,DISCOUNTED_SUM','catalog_only','One-step dynamic programming update','state-transition kernel required'),
 ('BACKWARD_INDUCTION','dynamic','value(s)=max_action continuation_value(s,action)','finite_game_tree','policy','ARGMAX','catalog_only','Finite perfect-information sequential solution','tree, information, terminal rewards required'),
 ('REGRET_MATCHING','learning','sigma(a) proportional max(R(a),0)','regret_vector','probability_list','MAX','catalog_only','Cumulative positive-regret strategy update','uniform strategy on zero positive regret'),
 ('COUNTERFACTUAL_REGRET','learning','R_i(I,a)=v_i(sigma_{I->a},I)-v_i(sigma,I)','extensive_game,strategy','regret_vector','EXPECTED_UTILITY','catalog_only','Counterfactual regret at information set','reach-weighting conventions required'),
 ('NASH_BARGAINING','bargaining','argmax_{u>=d} product_i(u_i-d_i)','feasible_set,disagreement','allocation','ARGMAX','catalog_only','Nash bargaining solution over feasible utilities','compact feasible set, disagreement point'),
 ('REPLICATOR_UPDATE','evolutionary','dx_i/dt=x_i(f_i(x)-sum_j x_j*f_j(x))','frequency_vector,fitness_vector','rate_vector','MULTIPLY,SUBTRACT','catalog_only','Population evolutionary dynamic','simplex frequency vector'),
 ('HAWK_VS_HAWK','game_payoff','(V-C)/2','number,number','number','SUBTRACT,DIVIDE','executable','Hawk against Hawk payoff under symmetric fight-cost sharing','0<V<C'),
 ('DOVE_VS_HAWK','game_payoff','0','number','number','','executable','Dove against Hawk payoff: yields without contest','V>0'),
 ('GENERAL_AUCTION_OPTIMAL_BID','auction','argmax_b E[U(b,B_rival,V) | information]','beliefs,utility,feasible_bids','number','PREDICT_ACTION,EXPECTED_UTILITY,ARGMAX','catalog_only','General Bayesian auction response','requires type/valuation model and feasible bid domain'),
]

OPERATORS = {}
for row in _ROWS:
    op_id,family,formula,inputs,output,deps,status,meaning,assumptions=row
    if op_id in OPERATORS:
        raise RuntimeError('duplicate math operator: '+op_id)
    OPERATORS[op_id]={
        'operator_id':op_id,'family':family,'formula':formula,
        'inputs':inputs.split(','),'output':output,
        'dependencies':list(filter(None,deps.split(','))),
        'status':status,'meaning':meaning,'assumptions':assumptions,
        'provenance':'synthetic_frozen_rule' if family=='qualitative' else 'mathematical_definition',
    }

# Complete existing 14-program V3 numeric catalog; aliases deliberately share semantics.
PROGRAM_ALIASES = {
 'ADD':'ADD','SUB':'SUBTRACT','RSUB':'REVERSE_SUBTRACT',
 'MUL':'MULTIPLY','ABSDIFF':'ABS_DIFFERENCE','MAX':'MAX','MIN':'MIN',
 'GT':'GREATER_THAN','ABS_X_GE_Y':'ABS_X_GE_Y','AVG':'AVERAGE',
 'IF_X_POS_Y_ELSE_X':'IF_X_POS_Y_ELSE_X',
 'TWICE_X_MINUS_Y':'TWICE_X_MINUS_Y','DOUBLE_SUM':'DOUBLE_SUM',
 'SQUARE_SUM':'SQUARE_SUM',
}

def frac(x: Any) -> Fraction:
    if isinstance(x,bool): raise TypeError('bool is not numeric')
    if isinstance(x,Fraction):return x
    if isinstance(x,(int,float,str)):
        if isinstance(x,float) and not math.isfinite(x):raise ValueError('nonfinite')
        return Fraction(str(x))
    raise TypeError('expected numeric value')

def out(v: Any) -> Any:
    """Lossless rational encoding, including non-integer values."""
    if isinstance(v,Fraction):
        return {'numerator':v.numerator,'denominator':v.denominator}
    if isinstance(v,tuple):return [out(x) for x in v]
    if isinstance(v,list):return [out(x) for x in v]
    if isinstance(v,dict):return {k:out(value) for k,value in v.items()}
    return v

def _probs(p):
    values=[frac(x) for x in p]
    if any(x<0 for x in values) or sum(values)!=1:
        raise ValueError('probabilities must sum to one')
    return values

def _list_nums(xs):
    r=[frac(x) for x in xs]
    if not r:raise ValueError('nonempty list required')
    return r

def execute(op: str, *args) -> Any:
    """Single runtime oracle; unsupported definitions fail closed."""
    spec=OPERATORS.get(op)
    if spec is None:raise KeyError(op)
    if spec['status']!='executable':raise NotImplementedError(op)
    if len(args)!=len(spec['inputs']):raise TypeError(f'{op}: expected {len(spec["inputs"])} inputs')
    if op=='LOWER_BOUND':
        bounds=args[0]
        if not isinstance(bounds,(tuple,list)) or len(bounds)!=2:raise ValueError('two interval endpoints required')
        lo,hi=map(frac,bounds)
        if not lo<hi:raise ValueError('invalid ordered interval')
        return lo
    if op=='ADD':return frac(args[0])+frac(args[1])
    if op=='SUBTRACT':return frac(args[0])-frac(args[1])
    if op=='MULTIPLY':return frac(args[0])*frac(args[1])
    if op=='DIVIDE':return frac(args[0])/frac(args[1])
    if op in ('MIDPOINT','AVERAGE'):return (frac(args[0])+frac(args[1]))/2
    if op=='REVERSE_SUBTRACT':return frac(args[1])-frac(args[0])
    if op=='ABS_DIFFERENCE':return abs(frac(args[0])-frac(args[1]))
    if op=='ABS_X_GE_Y':return int(abs(frac(args[0]))>=abs(frac(args[1])))
    if op=='IF_X_POS_Y_ELSE_X':return frac(args[1]) if frac(args[0])>0 else frac(args[0])
    if op=='TWICE_X_MINUS_Y':return 2*frac(args[0])-frac(args[1])
    if op=='DOUBLE_SUM':return 2*(frac(args[0])+frac(args[1]))
    if op=='SQUARE_SUM':return (frac(args[0])+frac(args[1]))**2
    if op=='MIN':return min(frac(args[0]),frac(args[1]))
    if op=='MAX':return max(frac(args[0]),frac(args[1]))
    if op=='GREATER_THAN':return int(frac(args[0])>frac(args[1]))
    if op=='CLAMP':
        x,l,h=map(frac,args)
        if l>h:raise ValueError('invalid interval')
        return min(max(x,l),h)
    if op in ('ARGMAX','BEST_RESPONSE'):
        xs=_list_nums(args[0]);return max(range(len(xs)),key=lambda i:xs[i])
    if op in ('EXPECTATION_DISCRETE','EXPECTED_UTILITY'):
        xs=_list_nums(args[0]);p=_probs(args[1]);
        if len(xs)!=len(p):raise ValueError('length mismatch')
        return sum(x*pi for x,pi in zip(xs,p))
    if op in ('CDF_UNIFORM','WIN_PROB_UNIFORM'):
        x,l,h=map(frac,args)
        if l>=h:raise ValueError('invalid uniform interval')
        return min(Fraction(1),max(Fraction(0),(x-l)/(h-l)))
    if op=='BAYES_UPDATE_BINARY':
        prior,lt,lf=map(frac,args)
        if any(v<0 or v>1 for v in (prior,lt,lf)):raise ValueError('invalid probabilities')
        evidence=prior*lt+(1-prior)*lf
        if evidence==0:raise ValueError('zero probability evidence')
        return prior*lt/evidence
    if op=='PURE_NASH':
        m=args[0]
        if not m or not m[0] or any(len(r)!=len(m[0]) for r in m):raise ValueError('invalid matrix')
        if any(len(cell)!=2 for r in m for cell in r):raise ValueError('two player payoffs required')
        nr,nc=len(m),len(m[0]);result=[]
        for r in range(nr):
            for c in range(nc):
                if (all(frac(m[r][c][0])>=frac(m[rr][c][0]) for rr in range(nr))
                    and all(frac(m[r][c][1])>=frac(m[r][cc][1]) for cc in range(nc))):
                    result.append([r,c])
        return result
    if op=='DISCOUNTED_SUM':
        rewards=_list_nums(args[0]);gamma=frac(args[1])
        if not 0<=gamma<=1:raise ValueError('invalid discount factor')
        return sum((gamma**i)*v for i,v in enumerate(rewards))
    if op=='SECOND_PRICE_UTILITY':return frac(args[0])-frac(args[1])
    if op=='FIRST_PRICE_EXPECTED_SURPLUS':
        v,b,lo,hi=map(frac,args)
        if v<0 or b<0 or lo<0 or lo>=hi:raise ValueError('invalid auction inputs')
        return (v-b)*execute('WIN_PROB_UNIFORM',b,lo,hi)
    if op=='OPTIMAL_BID_UNIFORM':
        v,l,h=map(frac,args)
        if v<0 or l<0 or l>=h:raise ValueError('invalid auction domain')
        if v<=l:return Fraction(0)
        return min(h,(v+l)/2)
    if op in ('LOW_PAYOFF','MEDIUM_PAYOFF','HIGH_PAYOFF','LOW_NEED','MEDIUM_NEED','HIGH_NEED'):
        coef={'LOW_PAYOFF':'0.5','MEDIUM_PAYOFF':'1','HIGH_PAYOFF':'1.5',
              'LOW_NEED':'0.5','MEDIUM_NEED':'0.75','HIGH_NEED':'1'}[op]
        return frac(args[0])*frac(coef)
    if op in ('LOW_INTELLIGENCE','MEDIUM_INTELLIGENCE','HIGH_INTELLIGENCE'):
        x=frac(args[0]);
        if x<0:raise ValueError('resources cannot be negative')
        uncertainty={'LOW_INTELLIGENCE':'0.5','MEDIUM_INTELLIGENCE':'0.25','HIGH_INTELLIGENCE':'0.1'}[op]
        u=frac(uncertainty)
        return (x*(1-u),x*(1+u))
    if op=='OWN_MEDIUM_NEED':return frac(args[0])
    if op=='HAWK_VS_HAWK':
        v,c=map(frac,args)
        if v<=0 or c<=v:raise ValueError('Hawk-Dove requires 0<V<C')
        return (v-c)/2
    if op=='DOVE_VS_HAWK':
        if frac(args[0])<=0:raise ValueError('value must be positive')
        return Fraction(0)
    if op=='HAWK_VS_DOVE':return frac(args[0])
    if op=='DOVE_VS_DOVE':return frac(args[0])/2
    if op=='LOGIT_RESPONSE':
        utilities=_list_nums(args[0]);strength=float(frac(args[1]))
        if not math.isfinite(strength) or strength<0:raise ValueError('invalid response strength')
        logits=[float(x)*strength for x in utilities]
        mx=max(logits);exps=[math.exp(x-mx) for x in logits];norm=sum(exps)
        return [v/norm for v in exps]
    raise NotImplementedError(op)

# Independent expected outputs for EVERY executable operator. Gold is specified
# literally, never computed from execute() to avoid tautological tests.
VECTORS={
 'ADD':(([2,3],'5'),), 'SUBTRACT':(([10,3],'7'),),
 'MULTIPLY':(([2,3],'6'),), 'DIVIDE':(([1,4],'1/4'),),
 'LOWER_BOUND':(([["62.5","187.5"]],'125/2'),),
 'MIDPOINT':(([100,'62.5'],'325/4'),),
 'REVERSE_SUBTRACT':(([10,3],'-7'),),
 'ABS_DIFFERENCE':(([10,3],'7'),),
 'AVERAGE':(([100,'62.5'],'325/4'),),
 'ABS_X_GE_Y':(([-10,9],1),([4,5],0)),
 'IF_X_POS_Y_ELSE_X':(([2,8],'8'),([-2,8],'-2')),
 'TWICE_X_MINUS_Y':(([9,5],'13'),),
 'DOUBLE_SUM':(([9,5],'28'),),
 'SQUARE_SUM':(([9,5],'196'),),
 'MIN':(([2,4],'2'),), 'MAX':(([2,4],'4'),),
 'GREATER_THAN':(([90,20],1),([20,90],0)),
 'CLAMP':(([10,0,7],'7'),), 'ARGMAX':(([[4,9,1]],1),),
 'EXPECTATION_DISCRETE':(([[10,20],['0.25','0.75']],'35/2'),),
 'CDF_UNIFORM':((['81.25','62.5','187.5'],'3/20'),),
 'BAYES_UPDATE_BINARY':((['0.5','0.8','0.2'],'4/5'),),
 'EXPECTED_UTILITY':(([[10,20],['0.25','0.75']],'35/2'),),
 'BEST_RESPONSE':(([[2,7,3]],1),),
 'PURE_NASH':(([[[[ -2,-2],[6,0]],[[0,6],[3,3]]]],[[0,1],[1,0]]),),
 'DISCOUNTED_SUM':(([[3,4,5],'0.5'],'25/4'),),
 'SECOND_PRICE_UTILITY':(([80,30],'50'),),
 'WIN_PROB_UNIFORM':((['81.25','62.5','187.5'],'3/20'),),
 'FIRST_PRICE_EXPECTED_SURPLUS':(([100,'81.25','62.5','187.5'],'45/16'),),
 'OPTIMAL_BID_UNIFORM':(([100,'62.5','187.5'],'325/4'),([40,50,100],'0')),
 'HAWK_VS_HAWK':(([6,10],'-2'),),
 'DOVE_VS_HAWK':(([6],'0'),),
 'LOW_PAYOFF':(([40],'20'),), 'MEDIUM_PAYOFF':(([40],'40'),),
 'HIGH_PAYOFF':(([60],'90'),),
 'LOW_INTELLIGENCE':(([125],['125/2','375/2']),),
 'MEDIUM_INTELLIGENCE':(([100],['75','125']),),
 'HIGH_INTELLIGENCE':(([100],['90','110']),),
 'LOW_NEED':(([100],'50'),), 'MEDIUM_NEED':(([100],'75'),),
 'HIGH_NEED':(([125],'125'),),
 'OWN_MEDIUM_NEED':(([100],'100'),),
 'HAWK_VS_DOVE':(([6],'6'),), 'DOVE_VS_DOVE':(([6],'3'),),
 'LOGIT_RESPONSE':(([[5,5],1],[0.5,0.5]),),
}

def verify_definitions():
    """Fail closed on missing definitions, implementation, vectors, cycles or bad math."""
    if not OPERATORS:raise AssertionError('EMPTY_OPERATOR_REGISTRY')
    if len(PROGRAM_ALIASES)!=14 or any(op not in OPERATORS for op in PROGRAM_ALIASES.values()):
        raise AssertionError('LEGACY_NUMERIC_PROGRAM_DRIFT')
    executable={k for k,v in OPERATORS.items() if v['status']=='executable'}
    if executable!=set(VECTORS):raise AssertionError('EXECUTABLE_VECTOR_COVERAGE '+repr(sorted(executable^set(VECTORS))))
    visited=set();active=set()
    def walk(op):
        if op in active:raise AssertionError('DEPENDENCY_CYCLE '+op)
        if op in visited:return
        active.add(op)
        for dep in OPERATORS[op]['dependencies']:
            if dep not in OPERATORS:raise AssertionError('UNDEFINED_DEPENDENCY '+dep)
            if OPERATORS[op]['status']=='executable' and OPERATORS[dep]['status']!='executable':
                raise AssertionError('NONEXECUTABLE_DEPENDENCY '+dep)
            walk(dep)
        active.remove(op);visited.add(op)
    for op,spec in OPERATORS.items():
        if not all(spec.get(field) for field in ('formula','meaning','inputs','output')):
            raise AssertionError('INCOMPLETE_DEFINITION '+op)
        walk(op)
    count=0
    for op,cases in VECTORS.items():
        for arguments,expected in cases:
            actual=execute(op,*arguments)
            if op=='LOGIT_RESPONSE':
                if len(actual)!=len(expected) or max(abs(x-y) for x,y in zip(actual,expected))>1e-12:
                    raise AssertionError('VECTOR_MISMATCH '+op)
            elif out(actual)!=out(_expected(expected)):
                raise AssertionError('VECTOR_MISMATCH '+op+' got '+repr(out(actual))+' expected '+repr(expected))
            count+=1
    # Counterexamples that a guessed label could otherwise pass.
    for op,args in [('DIVIDE',(1,0)),('BAYES_UPDATE_BINARY',(0,0,0)),
                    ('OPTIMAL_BID_UNIFORM',(100,50,50)),('CDF_UNIFORM',(1,2,2))]:
        try:execute(op,*args)
        except (ValueError,ZeroDivisionError):pass
        else:raise AssertionError('DOMAIN_NOT_ENFORCED '+op)
    return {'definitions':len(OPERATORS),'executable':len(executable),
            'catalog_only':len(OPERATORS)-len(executable),'vectors_checked':count,
            'domain_rejections_checked':4}

def _expected(e):
    if isinstance(e,str):return Fraction(e)
    if isinstance(e,list):return [_expected(x) for x in e]
    return e
