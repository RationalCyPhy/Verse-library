import enum
import re
from typing import List, Dict
import pickle
# from ourtool.automaton.hybrid_io_automaton import HybridIoAutomaton
# from pythonparser import Guard
import ast

from z3 import *
import sympy

import astunparse

class LogicTreeNode:
    def __init__(self, data, child = [], val = None, mode_guard = None):
        self.data = data 
        self.child = child
        self.val = val
        self.mode_guard = mode_guard

class GuardExpressionAst:
    def __init__(self, guard_list):
        self.ast_list = []
        for guard in guard_list:
            self.ast_list.append(guard.ast)
        self.cont_variables = {}
        self.varDict = {'t':Real('t')}

    def _build_guard(self, guard_str, agent):
        """
        Build solver for current guard based on guard string

        Args:
            guard_str (str): the guard string.
            For example:"And(v>=40-0.1*u, v-40+0.1*u<=0)"

        Returns:
            A Z3 Solver obj that check for guard.
            A symbol index dic obj that indicates the index
            of variables that involved in the guard.
        """
        cur_solver = Solver()
        # This magic line here is because SymPy will evaluate == to be False
        # Therefore we are not be able to get free symbols from it
        # Thus we need to replace "==" to something else
        sympy_guard_str = guard_str.replace("==", ">=")
        for vars in self.cont_variables:
            sympy_guard_str = sympy_guard_str.replace(vars, self.cont_variables[vars])

        symbols = list(sympy.sympify(sympy_guard_str, evaluate=False).free_symbols)
        symbols = [str(s) for s in symbols]
        tmp = list(self.cont_variables.values())
        symbols_map = {}
        for s in symbols:
            if s in tmp:
                key = list(self.cont_variables.keys())[list(self.cont_variables.values()).index(s)]
                symbols_map[s] = key

        for vars in reversed(self.cont_variables):
            guard_str = guard_str.replace(vars, self.cont_variables[vars])
        guard_str = self._handleReplace(guard_str)
        cur_solver.add(eval(guard_str))  # TODO use an object instead of `eval` a string
        return cur_solver, symbols_map

    def _handleReplace(self, input_str):
        """
        Replace variable in inputStr to self.varDic["variable"]
        For example:
            input
                And(y<=0,t>=0.2,v>=-0.1)
            output: 
                And(self.varDic["y"]<=0,self.varDic["t"]>=0.2,self.varDic["v"]>=-0.1)
        
        Args:
            input_str (str): original string need to be replaced
            keys (list): list of variable strings

        Returns:
            str: a string that all variables have been replaced into a desire form

        """
        idxes = []
        i = 0
        original = input_str
        keys = list(self.varDict.keys())

        keys.sort(key=lambda s: len(s))
        for key in keys[::-1]:
            for i in range(len(input_str)):
                if input_str[i:].startswith(key):
                    idxes.append((i, i + len(key)))
                    input_str = input_str[:i] + "@" * len(key) + input_str[i + len(key):]

        idxes = sorted(idxes)

        input_str = original
        for idx in idxes[::-1]:
            key = input_str[idx[0]:idx[1]]
            target = 'self.varDict["' + key + '"]'
            input_str = input_str[:idx[0]] + target + input_str[idx[1]:]
        return input_str

    def evaluate_guard_cont(self, agent, continuous_variable_dict, lane_map):
        res = False
        is_contained = False

        for cont_vars in continuous_variable_dict:
            self.cont_variables[cont_vars] = cont_vars.replace('.','_')
            self.varDict[cont_vars.replace('.','_')] = Real(cont_vars.replace('.','_'))

        z3_string = self.generate_z3_expression() 
        if isinstance(z3_string, bool):
            if z3_string:
                return True, True 
            else:
                return False, False

        cur_solver, symbols = self._build_guard(z3_string, agent)
        cur_solver.push()
        for symbol in symbols:
            cur_solver.add(self.varDict[symbol] >= continuous_variable_dict[symbols[symbol]][0])
            cur_solver.add(self.varDict[symbol] <= continuous_variable_dict[symbols[symbol]][1])
        if cur_solver.check() == sat:
            # The reachtube hits the guard
            cur_solver.pop()
            res = True
            
            # TODO: If the reachtube completely fall inside guard, break
            tmp_solver = Solver()
            tmp_solver.add(Not(cur_solver.assertions()[0]))
            for symbol in symbols:
                tmp_solver.add(self.varDict[symbol] >= continuous_variable_dict[symbols[symbol]][0])
                tmp_solver.add(self.varDict[symbol] <= continuous_variable_dict[symbols[symbol]][1])
            if tmp_solver.check() == unsat:
                print("Full intersect, break")
                is_contained = True

        return res, is_contained

    def generate_z3_expression(self):
        """
        The return value of this function will be a bool/str

        If without evaluating the continuous variables the result is True, then
        the guard will automatically be satisfied and is_contained will be True

        If without evaluating the continuous variables the result is False, th-
        en the guard will automatically be unsatisfied

        If the result is a string, then continuous variables will be checked to
        see if the guard can be satisfied 
        """
        res = []
        for node in self.ast_list:
            tmp = self._generate_z3_expression_node(node)
            if isinstance(tmp, bool):
                if not tmp:
                    return False
                else:
                    continue
            res.append(tmp)
        if res == []:
            return True
        elif len(res) == 1:
            return res[0]
        res = "And("+",".join(res)+")"
        return res

    def _generate_z3_expression_node(self, node):
        """
        Perform a DFS over expression ast and generate the guard expression
        The return value of this function can be a bool/str

        If without evaluating the continuous variables the result is True, then
        the guard condition will automatically be satisfied
        
        If without evaluating the continuous variables the result is False, then
        the guard condition will not be satisfied

        If the result is a string, then continuous variables will be checked to
        see if the guard can be satisfied
        """
        if isinstance(node, ast.BoolOp):
            # Check the operator
            # For each value in the boolop, check results
            if isinstance(node.op, ast.And):
                z3_str = []
                for i,val in enumerate(node.values):
                    tmp = self._generate_z3_expression_node(val)
                    if isinstance(tmp, bool):
                        if tmp:
                            continue 
                        else:
                            return False
                    z3_str.append(tmp)
                z3_str = 'And('+','.join(z3_str)+')'
                return z3_str
            elif isinstance(node.op, ast.Or):
                z3_str = []
                for val in node.values:
                    tmp = self._generate_z3_expression_node(val)
                    if isinstance(tmp, bool):
                        if tmp:
                            return True
                        else:
                            continue
                    z3_str.append(tmp)
                z3_str = 'Or('+','.join(z3_str)+')'
                return z3_str
            # If string, construct string
            # If bool, check result and discard/evaluate result according to operator
            pass 
        elif isinstance(node, ast.Constant):
            # If is bool, return boolean result
            if isinstance(node.value, bool):
                return node.value
            # Else, return raw expression
            else:
                expr = astunparse.unparse(node)
                expr = expr.strip('\n')
                return expr
        else:
            # For other cases, we can return the expression directly
            expr = astunparse.unparse(node)
            expr = expr.strip('\n')
            return expr

    def evaluate_guard_disc(self, agent, discrete_variable_dict, lane_map):
        """
        Evaluate guard that involves only discrete variables. 
        """
        res = True
        for i, node in enumerate(self.ast_list):
            tmp, self.ast_list[i] = self._evaluate_guard_disc(node, agent, discrete_variable_dict, lane_map)
            res = res and tmp 
        return res
            
    def _evaluate_guard_disc(self, root, agent, disc_var_dict, lane_map):
        if isinstance(root, ast.Compare):
            expr = astunparse.unparse(root)
            if any([var in expr for var in disc_var_dict]):
                left, root.left = self._evaluate_guard_disc(root.left, agent, disc_var_dict, lane_map)
                right, root.comparators[0] = self._evaluate_guard_disc(root.comparators[0], agent, disc_var_dict, lane_map)
                if isinstance(root.ops[0], ast.GtE):
                    res = left>=right
                elif isinstance(root.ops[0], ast.Gt):
                    res = left>right 
                elif isinstance(root.ops[0], ast.Lt):
                    res = left<right
                elif isinstance(root.ops[0], ast.LtE):
                    res = left<=right
                elif isinstance(root.ops[0], ast.Eq):
                    res = left == right 
                elif isinstance(root.ops[0], ast.NotEq):
                    res = left != right 
                else:
                    raise ValueError(f'Node type {root} from {astunparse.unparse(root)} is not supported')
                if res:
                    root = ast.parse('True').body[0].value
                else:
                    root = ast.parse('False').body[0].value    
                return res, root
            else:
                return True, root
        elif isinstance(root, ast.BoolOp):
            if isinstance(root.op, ast.And):
                res = True
                for i,val in enumerate(root.values):
                    tmp,root.values[i] = self._evaluate_guard_disc(val, agent, disc_var_dict, lane_map)
                    res = res and tmp
                    if not res:
                        break
                return res, root
            elif isinstance(root.op, ast.Or):
                res = False
                for val in root.values:
                    tmp,val = self._evaluate_guard_disc(val, agent, disc_var_dict, lane_map)
                    res = res or tmp
                    if res:
                        break
                return res, root     
        elif isinstance(root, ast.BinOp):
            return True, root
        elif isinstance(root, ast.Call):
            expr = astunparse.unparse(root)
            # Check if the root is a function
            if any([var in expr for var in disc_var_dict]):
                # tmp = re.split('\(|\)',expr)
                # while "" in tmp:
                #     tmp.remove("")
                # for arg in tmp[1:]:
                #     if arg in disc_var_dict:
                #         expr = expr.replace(arg,f'"{disc_var_dict[arg]}"')
                # res = eval(expr)
                for arg in disc_var_dict:
                    expr = expr.replace(arg, f'"{disc_var_dict[arg]}"')
                res = eval(expr)
                if isinstance(res, bool):
                    if res:
                        root = ast.parse('True').body[0].value
                    else:
                        root = ast.parse('False').body[0].value    
                return res, root
            else:
                return True, root
        elif isinstance(root, ast.Attribute):
            expr = astunparse.unparse(root)
            expr = expr.strip('\n')
            if expr in disc_var_dict:
                val = disc_var_dict[expr]
                for mode_name in agent.controller.modes:
                    if val in agent.controller.modes[mode_name]:
                        val = mode_name+'.'+val
                        break
                return val, root
            elif root.value.id in agent.controller.modes:
                return expr, root
            else:
                return True, root
        elif isinstance(root, ast.Constant):
            return root.value, root
        else:
            raise ValueError(f'Node type {root} from {astunparse.unparse(root)} is not supported')

    def evaluate_guard(self, agent, continuous_variable_dict, discrete_variable_dict, lane_map):
        res = True
        for node in self.ast_list:
            tmp = self._evaluate_guard(node, agent, continuous_variable_dict, discrete_variable_dict, lane_map)
            res = tmp and res
            if not res:
                break
        return res

    def _evaluate_guard(self, root, agent, cnts_var_dict, disc_var_dict, lane_map):
        if isinstance(root, ast.Compare):
            left = self._evaluate_guard(root.left, agent, cnts_var_dict, disc_var_dict, lane_map)
            right = self._evaluate_guard(root.comparators[0], agent, cnts_var_dict, disc_var_dict, lane_map)
            if isinstance(root.ops[0], ast.GtE):
                return left>=right
            elif isinstance(root.ops[0], ast.Gt):
                return left>right 
            elif isinstance(root.ops[0], ast.Lt):
                return left<right
            elif isinstance(root.ops[0], ast.LtE):
                return left<=right
            elif isinstance(root.ops[0], ast.Eq):
                return left == right 
            elif isinstance(root.ops[0], ast.NotEq):
                return left != right 
            else:
                raise ValueError(f'Node type {root} from {astunparse.unparse(root)} is not supported')

        elif isinstance(root, ast.BoolOp):
            if isinstance(root.op, ast.And):
                res = True
                for val in root.values:
                    tmp = self._evaluate_guard(val, agent, cnts_var_dict, disc_var_dict, lane_map)
                    res = res and tmp
                    if not res:
                        break
                return res
            elif isinstance(root.op, ast.Or):
                res = False
                for val in root.values:
                    tmp = self._evaluate_guard(val, agent, cnts_var_dict, disc_var_dict, lane_map)
                    res = res or tmp
                    if res:
                        break
                return res
        elif isinstance(root, ast.BinOp):
            left = self._evaluate_guard(root.left, agent, cnts_var_dict, disc_var_dict, lane_map)
            right = self._evaluate_guard(root.right, agent, cnts_var_dict, disc_var_dict, lane_map)
            if isinstance(root.op, ast.Sub):
                return left - right
            elif isinstance(root.op, ast.Add):
                return left + right
            else:
                raise ValueError(f'Node type {root} from {astunparse.unparse(root)} is not supported')
        elif isinstance(root, ast.Call):
            expr = astunparse.unparse(root)
            # Check if the root is a function
            if 'map' in expr:
                # tmp = re.split('\(|\)',expr)
                # while "" in tmp:
                #     tmp.remove("")
                # for arg in tmp[1:]:
                #     if arg in disc_var_dict:
                #         expr = expr.replace(arg,f'"{disc_var_dict[arg]}"')
                # res = eval(expr)
                for arg in disc_var_dict:
                    expr = expr.replace(arg, f'"{disc_var_dict[arg]}"')
                for arg in cnts_var_dict:
                    expr = expr.replace(arg, str(cnts_var_dict[arg]))    
                res = eval(expr)
                return res
        elif isinstance(root, ast.Attribute):
            expr = astunparse.unparse(root)
            expr = expr.strip('\n')
            if expr in disc_var_dict:
                val = disc_var_dict[expr]
                for mode_name in agent.controller.modes:
                    if val in agent.controller.modes[mode_name]:
                        val = mode_name+'.'+val
                        break
                return val
            elif expr in cnts_var_dict:
                val = cnts_var_dict[expr]
                return val
            elif root.value.id in agent.controller.modes:
                return expr
        elif isinstance(root, ast.Constant):
            return root.value
        else:
            raise ValueError(f'Node type {root} from {astunparse.unparse(root)} is not supported')

if __name__ == "__main__":
    with open('tmp.pickle','rb') as f:
        guard_list = pickle.load(f)
    tmp = GuardExpressionAst(guard_list)
    # tmp.evaluate_guard()
    # tmp.construct_tree_from_str('(other_x-ego_x<20) and other_x-ego_x>10 and other_vehicle_lane==ego_vehicle_lane')
    print("stop")