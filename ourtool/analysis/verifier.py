from typing import List, Dict
import copy

import numpy as np

from ourtool.agents.base_agent import BaseAgent
from ourtool.analysis.analysis_tree_node import AnalysisTreeNode
from ourtool.dryvr.core.dryvrcore import calc_bloated_tube
import ourtool.dryvr.common.config as userConfig

class Verifier:
    def __init__(self):
        self.reachtube_tree_root = None
        self.unsafe_set = None
        self.verification_result = None 

    def compute_full_reachtube(
        self, 
        init_list, 
        init_mode_list, 
        agent_list:List[BaseAgent], 
        transition_graph, 
        time_horizon, 
        lane_map
    ):
        root = AnalysisTreeNode()
        for i, agent in enumerate(agent_list):
            root.init[agent.id] = init_list[i]
            init_mode = [elem.name for elem in init_mode_list[i]]
            init_mode =','.join(init_mode)
            root.mode[agent.id] = init_mode 
            root.agent[agent.id] = agent 
        self.reachtube_tree_root = root 
        verification_queue = []
        verification_queue.append(root)
        while verification_queue != []:
            node:AnalysisTreeNode = verification_queue.pop(0)
            print(node.mode)
            remain_time = time_horizon - node.start_time 
            if remain_time <= 0:
                continue 
            # For reachtubes not already computed
            for agent_id in node.agent:
                if agent_id not in node.trace:
                    # Compute the trace starting from initial condition
                    mode = node.mode[agent_id]
                    init = node.init[agent_id]
                    # trace = node.agent[agent_id].TC_simulate(mode, init, remain_time,lane_map)
                    # trace[:,0] += node.start_time
                    # node.trace[agent_id] = trace.tolist()

                    cur_bloated_tube = calc_bloated_tube(mode,
                                        init,
                                        remain_time,
                                        node.agent[agent_id].TC_simulate,
                                        'GLOBAL',
                                        None,
                                        userConfig.SIMTRACENUM,
                                        lane_map = lane_map
                                        )
                    trace = np.array(cur_bloated_tube)
                    trace[:,0] += node.start_time
                    node.trace[agent_id] = trace.tolist()
                    # print("here")
            
            # Check safety conditions here

            # Get all possible transitions to next mode
            all_possible_transitions = transition_graph.get_all_transition_set(node)
            max_end_idx = 0
            for transition in all_possible_transitions:
                transit_agent_idx, src_mode, dest_mode, next_init, idx = transition 
                start_idx, end_idx = idx
 
                truncated_trace = {}
                for agent_idx in node.agent:
                    truncated_trace[agent_idx] = node.trace[agent_idx][start_idx*2:]
                if end_idx > max_end_idx:
                    max_end_idx = end_idx
                next_node_mode = copy.deepcopy(node.mode) 
                next_node_mode[transit_agent_idx] = dest_mode 
                next_node_agent = node.agent 
                next_node_start_time = list(truncated_trace.values())[0][0][0]
                next_node_init = {}
                next_node_trace = {}
                for agent_idx in next_node_agent:
                    if agent_idx == transit_agent_idx:
                        next_node_init[agent_idx] = next_init 
                    else:
                        next_node_trace[agent_idx] = truncated_trace[agent_idx]
                
                tmp = AnalysisTreeNode(
                    trace = next_node_trace,
                    init = next_node_init,
                    mode = next_node_mode,
                    agent = next_node_agent,
                    child = [],
                    start_time = next_node_start_time
                )
                node.child.append(tmp)
                verification_queue.append(tmp)

            """Truncate trace of current node based on max_end_idx"""
            for agent_idx in node.agent:
                node.trace[agent_idx] = node.trace[agent_idx][:(max_end_idx+1)*2]
        