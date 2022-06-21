import ast, copy
from typing import List, Dict, Union, Optional, Any, Tuple
from dataclasses import dataclass, field, fields
from enum import Enum, auto
import dryvr_plus_plus.scene_verifier.code_parser.astunparser as astunparser

debug = False

def dbg(msg, *rest):
    if not debug:
        return rest
    print(f"\x1b\x5b31m{msg}\x1b\x5bm", end="")
    for i, a in enumerate(rest[:5]):
        print(f" \x1b\x5b3{i+2}m{a}\x1b\x5bm", end="")
    if rest[5:]:
        print("", rest[5:])
    else:
        print()
    return rest

def not_ir_ast(a) -> bool:
    """Is not some type that can be used in AST substitutions"""
    return isinstance(a, ast.arg)

def fully_cond(a) -> bool:
    """Check that the values in the whole tree is based on some conditions"""
    if isinstance(a, CondVal):
        return not dbg("cv", all(len(e.cond) == 0 for e in a.elems))
    if isinstance(a, dict):
        return dbg("obj", all(fully_cond(o) for o in a.values()))
    if isinstance(a, Lambda):
        return dbg("lambda", fully_cond(a.body))
    if not_ir_ast(a):
        return True
    return False

@dataclass
class ModeDef:
    modes: List[str] = field(default_factory=list)

@dataclass
class StateDef:
    """Variable/member set needed for simulation/verification for some object"""
    cont: List[str] = field(default_factory=list)     # Continuous variables
    disc: List[str] = field(default_factory=list)     # Discrete variables
    static: List[str] = field(default_factory=list)   # Static data in object

ScopeValue = Union[ast.AST, "CondVal", "Lambda", "Reduction", Dict[str, "ScopeValue"]]

@dataclass
class CondValCase:
    """A single case of a conditional value. Values in `cond` are implicitly `and`ed together"""
    cond: List[ScopeValue]
    val: ScopeValue

    def __eq__(self, o) -> bool:
        if o == None or len(self.cond) != len(o.cond):
            return False
        return all(ControllerIR.ir_eq(sc, oc) for sc, oc in zip(self.cond, o.cond)) and ControllerIR.ir_eq(self.val, o.val)

@dataclass
class CondVal:
    """A conditional value. Actual value is the combined result from all the cases"""
    elems: List[CondValCase]

class ReductionType(Enum):
    Any = auto()
    All = auto()
    Max = auto()
    Min = auto()
    Sum = auto()

    @staticmethod
    def from_str(s: str) -> "ReductionType":
        return getattr(ReductionType, s.title())

    def __str__(self) -> str:
        return {
            ReductionType.Any: "any",
            ReductionType.All: "all",
            ReductionType.Max: "max",
            ReductionType.Min: "min",
            ReductionType.Sum: "sum",
        }[self]

@dataclass(unsafe_hash=True)
class Reduction:
    """A simple reduction. Must be a reduction function (see `ReductionType`) applied to a generator
    with a single clause over a iterable"""
    op: ReductionType
    expr: ast.expr
    it: str
    value: ast.AST

    def __eq__(self, o) -> bool:
        if o == None:
            return False
        return self.op == o.op and self.it == o.it and ControllerIR.ir_eq(self.expr, o.expr) and ControllerIR.ir_eq(self.value, o.value)

    def __repr__(self) -> str:
        return f"Reduction('{self.op}', expr={astunparser.unparse(self.expr)}, it='{self.it}', value={astunparser.unparse(self.value)}"

Reduction._fields = [f.name for f in fields(Reduction)]

@dataclass
class Lambda:
    """A closure. Comes from either a `lambda` or a `def`ed function"""
    args: List[Tuple[str, Optional[str]]]
    body: ast.expr

    @staticmethod
    def from_ast(tree: Union[ast.FunctionDef, ast.Lambda], env: "Env") -> "Lambda":
        args = []
        for a in tree.args.args:
            if a.annotation != None:
                if isinstance(a.annotation, ast.Constant):
                    typ = a.annotation.value
                elif isinstance(a.annotation, ast.Name):
                    typ = a.annotation.id
                else:
                    raise TypeError("weird annotation?")
                args.append((a.arg, typ))
            else:
                args.append((a.arg, None))
        env.push()
        for a, _ in args:
            env.add_hole(a)
        ret = None
        if isinstance(tree, ast.FunctionDef):
            for node in tree.body:
                ret = proc(node, env)
        elif isinstance(tree, ast.Lambda):
            ret = proc(tree.body, env)
        env.pop()
        assert ret != None, "Empty function"
        return Lambda(args, ret)

    def apply(self, args: List[ast.expr]) -> ast.expr:
        ret = copy.deepcopy(self.body)
        return ArgSubstituter({k: v for (k, _), v in zip(self.args, args)}).visit(ret)

ast_dump = lambda node, dump=False: ast.dump(node) if dump else astunparser.unparse(node)

ScopeLevel = Dict[str, ScopeValue]

class ArgSubstituter(ast.NodeTransformer):
    args: Dict[str, ast.expr]
    def __init__(self, args: Dict[str, ast.expr]):
        super().__init__()
        self.args = args

    def visit_arg(self, node):
        if node.arg in self.args:
            return self.args[node.arg]
        self.generic_visit(node)
        return node

@dataclass
class ControllerIR:
    controller: Lambda
    state_defs: Dict[str, StateDef]
    mode_defs: Dict[str, ModeDef]

    @staticmethod
    def parse(code: Optional[str] = None, fn: Optional[str] = None) -> "ControllerIR":
        return Env.parse(code, fn).to_ir()

    @staticmethod
    def empty() -> "ControllerIR":
        return ControllerIR(Lambda(args=[], body=ast.Constant({})), {}, {})

    @staticmethod
    def dump(node, dump=False):
        if node == None:
            return "None"
        if isinstance(node, ast.arg):
            return f"Hole({node.arg})"
        if isinstance(node, (ModeDef, StateDef)):
            return f"<{node}>"
        if isinstance(node, Lambda):
            return f"<Lambda args: {node.args} body: {ControllerIR.dump(node.body, dump)}>"
        if isinstance(node, CondVal):
            return f"<CondVal{''.join(f' [{ControllerIR.dump(e.val, dump)} if {ControllerIR.dump(e.cond, dump)}]' for e in node.elems)}>"
        if isinstance(node, ast.If):
            return f"<{{{ast_dump(node, dump)}}}>"
        if isinstance(node, Reduction):
            return f"<Reduction {node.op} {ast_dump(node.expr, dump)} for {node.it} in {ast_dump(node.value, dump)}>"
        elif isinstance(node, dict):
            return "<Object " + " ".join(f"{k}: {ControllerIR.dump(v, dump)}" for k, v in node.items()) + ">"
        elif isinstance(node, list):
            return f"[{', '.join(ControllerIR.dump(n, dump) for n in node)}]"
        else:
            return ast_dump(node, dump)

    @staticmethod
    def ir_eq(a: Optional[ScopeValue], b: Optional[ScopeValue]) -> bool:
        """Equality check on the "IR" nodes"""
        return ControllerIR.dump(a) == ControllerIR.dump(b)     # FIXME Proper equality checks; dump needed cuz asts are dumb

    def getNextModes(self) -> List[Any]:
        controller_body = self.controller.body 
        paths = []
        for variable in controller_body:
            cond_val: CondVal = controller_body[variable]
            for case in cond_val.elems:
                val = case.val 
                guard = case.cond
                reset = (variable, val)
                paths.append((guard, reset))
        return paths 

@dataclass
class Env():
    state_defs: Dict[str, StateDef] = field(default_factory=dict)
    mode_defs: Dict[str, ModeDef] = field(default_factory=dict)
    scopes: List[ScopeLevel] = field(default_factory=lambda: [{}])

    @staticmethod
    def parse(code: Optional[str] = None, fn: Optional[str] = None):
        if code != None:
            if fn != None:
                root = ast.parse(code, fn)
            else:
                root = ast.parse(code)
        elif fn != None:
            with open(fn) as f:
                cont = f.read()
            root = ast.parse(cont, fn)
        else:
            raise TypeError("need at least one of `code` and `fn`")
        env = Env()
        proc(root, env)
        return env

    def push(self):
        self.scopes = [{}] + self.scopes

    def pop(self):
        self.scopes = self.scopes[1:]

    def lookup(self, key):
        for env in self.scopes:
            if key in env:
                return env[key]
        return None

    def set(self, key, val):
        for env in self.scopes:
            if key in env:
                env[key] = val
                return
        self.scopes[0][key] = val

    def add_hole(self, name: str):
        self.set(name, ast.arg(name, None))

    @staticmethod
    def dump_scope(env: ScopeLevel, dump=False):
        print("+++")
        for k, node in env.items():
            print(f"{k}: {ControllerIR.dump(node, dump)}")
        print("---")

    def dump(self, dump=False):
        print("{{{")
        for env in self.scopes:
            self.dump_scope(env, dump)
        print("}}}")

    @staticmethod
    def trans_args(sv: ScopeValue) -> ScopeValue:
        """Finish up parsing to turn `ast.arg` placeholders into `ast.Name`s so that the trees can be easily evaluated later"""
        class ArgTransformer(ast.NodeTransformer):
            def visit_arg(self, node):
                return ast.Name(node.arg, ctx=ast.Load())

            def visit_Attribute(self, node):
                if isinstance(node.value, ast.Name):
                    return ast.Name(f"{node.value.id}.{node.attr}", ctx=ast.Load())
                return node

        if isinstance(sv, dict):
            for k, v in sv.items():
                sv[k] = Env.trans_args(v)
            return sv
        if isinstance(sv, CondVal):
            for i, case in enumerate(sv.elems):
                sv.elems[i].val = Env.trans_args(case.val)
                for j, cond in enumerate(case.cond):
                    sv.elems[i].cond[j] = Env.trans_args(cond)
            return sv
        if isinstance(sv, ast.AST):
            return ArgTransformer().visit(sv)
        if isinstance(sv, Lambda):
            sv.body = Env.trans_args(sv.body)
            return sv
        if isinstance(sv, Reduction):
            sv.expr = Env.trans_args(sv.expr)
            sv.value = Env.trans_args(sv.value)
            return sv

    def to_ir(self):
        top = self.scopes[0]
        assert fully_cond(top)
        if 'controller' not in top or not isinstance(top['controller'], Lambda):
            raise TypeError("can't find controller")
        controller = Env.trans_args(top['controller'])
        assert isinstance(controller, Lambda)
        return ControllerIR(controller, self.state_defs, self.mode_defs)

def merge_if(test: ast.expr, trues: Env, falses: Env, env: Env):
    # `true`, `false` and `env` should have the same level
    for true, false in zip(trues.scopes, falses.scopes):
        merge_if_single(test, true, false, env)

def merge_if_single(test, true: ScopeLevel, false: ScopeLevel, scope: Union[Env, ScopeLevel]):
    dbg("merge if single", ControllerIR.dump(test), true.keys(), false.keys())
    def lookup(s, k):
        if isinstance(s, Env):
            return s.lookup(k)
        return s.get(k)
    def assign(s, k, v):
        if isinstance(s, Env):
            s.set(k, v)
        else:
            s[k] = v
    for var in set(true.keys()).union(set(false.keys())):
        var_true, var_false = true.get(var), false.get(var)
        if ControllerIR.ir_eq(var_true, var_false):
            continue
        if var_true != None and var_false != None:
            assert isinstance(var_true, dict) == isinstance(var_false, dict)
        dbg("merge", var, ControllerIR.dump(test), ControllerIR.dump(var_true), ControllerIR.dump(var_false))
        if isinstance(var_true, dict):
            if not isinstance(lookup(scope, var), dict):
                if lookup(scope, var) != None:
                    dbg("???", var, lookup(scope, var))
                dbg("if.merge.obj.init")
                assign(scope, var, {})
            var_true_emp, var_false_emp, var_scope = true.get(var, {}), false.get(var, {}), lookup(scope, var)
            dbg(isinstance(var_true_emp, dict), isinstance(var_false_emp, dict), isinstance(var_scope, dict))
            assert isinstance(var_true_emp, dict) and isinstance(var_false_emp, dict) and isinstance(var_scope, dict)
            merge_if_single(test, var_true_emp, var_false_emp, var_scope)
        else:
            if_val = merge_if_val(test, var_true, var_false, lookup(scope, var))
            dbg(ControllerIR.dump(if_val))
            assign(scope, var, if_val)
        dbg("merged", var, ControllerIR.dump(lookup(scope, var)))

def merge_if_val(test, true: Optional[ScopeValue], false: Optional[ScopeValue], orig: Optional[ScopeValue]) -> CondVal:
    dbg("merge val", ControllerIR.dump(test), ControllerIR.dump(true), ControllerIR.dump(false), ControllerIR.dump(orig), false == orig)
    def merge_cond(test, val):
        if isinstance(val, CondVal):
            for elem in val.elems:
                elem.cond.append(test)
            return val
        else:
            return CondVal([CondValCase([test], val)])
    def as_cv(a):
        if a == None:
            return None
        if not isinstance(a, CondVal):
            return CondVal([CondValCase([], a)])
        return a
    true, false, orig = as_cv(true), as_cv(false), as_cv(orig)
    dbg("merge convert", ControllerIR.dump(true), ControllerIR.dump(false), ControllerIR.dump(orig))
    if orig != None:
        for orig_cve in orig.elems:
            if true != None and orig_cve in true.elems:
                true.elems.remove(orig_cve)
            if false != None and orig_cve in false.elems:
                false.elems.remove(orig_cve)

    dbg("merge diff", ControllerIR.dump(test), ControllerIR.dump(true), ControllerIR.dump(false), ControllerIR.dump(orig))
    true_emp, false_emp = true == None or len(true.elems) == 0, false == None or len(false.elems) == 0
    if true_emp and false_emp:
        raise Exception("no need for merge?")
    elif true_emp:
        ret = merge_cond(ast.UnaryOp(ast.Not(), test), false)
    elif false_emp:
        ret = merge_cond(test, true)
    else:
        merge_true, merge_false = merge_cond(test, true), merge_cond(ast.UnaryOp(ast.Not(), test), false)
        ret = CondVal(merge_true.elems + merge_false.elems)
    if orig != None:
        return CondVal(ret.elems + orig.elems)
    return ret

def proc_assign(target: ast.AST, val, env: Env):
    dbg("proc_assign", astunparser.unparse(target), val)
    if isinstance(target, ast.Name):
        if isinstance(val, ast.AST):
            val = proc(val, env)
            if val != None:
                env.set(target.id, val)
        else:
            env.set(target.id, val)
    elif isinstance(target, ast.Attribute):
        if proc(target.value, env) == None:
            dbg("proc.assign.obj.init")
            proc_assign(target.value, {}, env)
        obj = proc(target.value, env)
        if isinstance(val, ast.AST):
            val = proc(val, env)
            if val != None:
                obj[target.attr] = val
        else:
            obj[target.attr] = val
    else:
        raise NotImplementedError("assign.others")

def is_main_check(node: ast.If) -> bool:
    check_comps = lambda a, b: (isinstance(a, ast.Name) and a.id == "__name__"
                                and isinstance(b, ast.Constant) and b.value == "__main__")
    return (isinstance(node.test, ast.Compare)
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and (check_comps(node.test.left, node.test.comparators[0])
             or check_comps(node.test.comparators[0], node.test.left)))

START_OF_MAIN = "--start-of-main--"

# NOTE `ast.arg` used as a placeholder for idents we don't know the value of.
# This is fine as it's never used in expressions
def proc(node: ast.AST, env: Env) -> Any:
    if isinstance(node, ast.Module):
        for node in node.body:
            if proc(node, env) == START_OF_MAIN:
                break
    elif not_ir_ast(node):
        return node
    # Data massaging
    elif isinstance(node, ast.For) or isinstance(node, ast.While):
        raise NotImplementedError("loops not supported")
    elif isinstance(node, ast.If):
        if is_main_check(node):
            return START_OF_MAIN
        test = proc(node.test, env)
        true_scope = copy.deepcopy(env)
        for true in node.body:
            proc(true, true_scope)
        false_scope = copy.deepcopy(env)
        for false in node.orelse:
            proc(false, false_scope)
        merge_if(test, true_scope, false_scope, env)

    # Definition/Assignment
    elif isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
        for alias in node.names:
            env.add_hole(alias.name if alias.asname == None else alias.asname)
    elif isinstance(node, ast.Assign):
        if len(node.targets) == 1:
            proc_assign(node.targets[0], node.value, env)
        else:
            raise NotImplementedError("unpacking not supported")
    elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return env.lookup(node.id)
    elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        obj = proc(node.value, env)
        # TODO since we know what the mode and state types contain we can do some typo checking
        if not_ir_ast(obj):
            # return node
            return ast.arg(f"{obj.arg}.{node.attr}", None)
        return obj[node.attr]
    elif isinstance(node, ast.FunctionDef):
        env.set(node.name, Lambda.from_ast(node, env))
    elif isinstance(node, ast.Lambda):
        return Lambda.from_ast(node, env)
    elif isinstance(node, ast.ClassDef):
        def grab_names(nodes: List[ast.stmt]):
            names = []
            for node in nodes:
                if isinstance(node, ast.Assign):
                    if len(node.targets) > 1:
                        raise NotImplementedError("multiple mode/state names at once")
                    if isinstance(node.targets[0], ast.Name):
                        names.append(node.targets[0].id)
                    else:
                        raise NotImplementedError("non ident as mode/state name")
                elif isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name):
                        names.append(node.target.id)
                    else:
                        raise NotImplementedError("non ident as mode/state name")
            return names

        # NOTE we are dupping it in `state_defs`/`mode_defs` and the scopes cuz value
        if node.name.endswith("Mode"):
            mode_def = ModeDef(grab_names(node.body))
            env.mode_defs[node.name] = mode_def
        elif node.name.endswith("State"):
            names = grab_names(node.body)
            state_vars = StateDef()
            for name in names:
                if "type" == name:
                    state_vars.static.append(name)
                elif "mode" not in name:
                    state_vars.cont.append(name)
                else:
                    state_vars.disc.append(name)
            env.state_defs[node.name] = state_vars
        env.add_hole(node.name)

    # Expressions
    elif isinstance(node, ast.UnaryOp):
        return ast.UnaryOp(node.op, proc(node.operand, env))
    elif isinstance(node, ast.BinOp):
        return ast.BinOp(proc(node.left, env), node.op, proc(node.right, env))
    elif isinstance(node, ast.BoolOp):
        return ast.BoolOp(node.op, [proc(val, env) for val in node.values])
    elif isinstance(node, ast.Compare):
        if len(node.ops) > 1 or len(node.comparators) > 1:
            raise NotImplementedError("too many comparisons")
        return ast.Compare(proc(node.left, env), node.ops, [proc(node.comparators[0], env)])
    elif isinstance(node, ast.Call):
        fun = proc(node.func, env)
        if isinstance(fun, Lambda):
            return fun.apply([proc(a, env) for a in node.args])
        if isinstance(fun, ast.arg):
            if fun.arg == "copy.deepcopy":
                ret = None
            else:
                ret = copy.deepcopy(node)
                ret.args = [proc(a, env) for a in ret.args]
            return ret
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in ["any", "all"]:#, "max", "min", "sum"]:      # TODO
                raise NotImplementedError(f"builtin function? {name}")
            if len(node.args) != 1 or not isinstance(node.args[0], ast.GeneratorExp):
                raise NotImplementedError("reduction on non-generators")
            gen = node.args[0]
            if len(gen.generators) != 1:
                raise NotImplementedError("multiple generator clauses")
            op = ReductionType.from_str(name)
            expr = gen.elt
            gen = gen.generators[0]
            target, ifs, iter = gen.target, gen.ifs, gen.iter
            if not isinstance(target, ast.Name):
                raise NotImplementedError("complex generator target")
            def cond_trans(e: ast.expr, c: ast.expr) -> ast.expr:
                if op == ReductionType.Any:
                    return ast.BoolOp(ast.And(), [e, c])
                else:
                    return ast.BoolOp(ast.Or(), [e, ast.UnaryOp(ast.Not(), c)])
            env.push()
            env.add_hole(target.id)
            expr = proc(expr, env)
            env.pop()
            expr = cond_trans(expr, ast.BoolOp(ast.And(), ifs)) if len(ifs) > 0 else expr
            return Reduction(op, expr, target.id, proc(iter, env))
    elif isinstance(node, ast.Return):
        return proc(node.value, env) if node.value != None else None
    elif isinstance(node, ast.IfExp):
        return ast.If(node.test, [node.body], [node.orelse])

    # Literals
    elif isinstance(node, ast.List):
        return ast.List([proc(e, env) for e in node.elts])
    elif isinstance(node, ast.Tuple):
        return ast.Tuple([proc(e, env) for e in node.elts])
    elif isinstance(node, ast.Constant):
        return node         # XXX simplification?
    else:
        raise NotImplementedError(str(node.__class__))

if __name__ == "__main__":
    # import sys
    # if len(sys.argv) != 2:
    #     print("usage: parse.py <file.py>")
    #     sys.exit(1)
    # fn = sys.argv[1]
    fn = "./demo/ball_bounces.py"
    # fn = "./demo/example_two_car_sign_lane_switch.py"
    e = Env.parse(fn=fn)
    tmp = e.to_ir()
    e.dump()
    print(ControllerIR.dump(e.to_ir().controller.body, False))
