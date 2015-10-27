from action import Action, SHIFT, NODE, IMPLICIT, REDUCE, SWAP, FINISH
from config import Config
from ucca import layer1

ROOT_ID = "1.1"  # ID of root node in UCCA passages


class Oracle:
    """
    Oracle to produce gold transition parses given UCCA passages
    To be used for creating training data for a transition-based UCCA parser
    :param passage gold passage to get the correct edges from
    """
    def __init__(self, passage):
        self.nodes_remaining = {node.ID for node in passage.layer(layer1.LAYER_ID).all} - {ROOT_ID}
        self.edges_remaining = {edge for node in passage.nodes.values() for edge in node}
        self.passage = passage

    def get_action(self, state):
        """
        Determine best action according to current state
        :param state: current State of the parser
        :return: best Action to perform
        """
        if not self.edges_remaining:
            return FINISH

        if state.stack:
            incoming = self.edges_remaining.intersection(state.stack[-1].orig_node.incoming)
            outgoing = self.edges_remaining.intersection(state.stack[-1].orig_node.outgoing)
            if not incoming | outgoing:
                return REDUCE

            related = set([edge.child.ID for edge in outgoing] +
                          [edge.parent.ID for edge in incoming])
            # prefer incorporating immediate relatives if possible
            if state.buffer and state.buffer[0].node_id in related:
                return SHIFT

            if len(state.stack) > 1:
                # check for binary edges
                for edges, prefix in (((e for e in incoming if
                                        e.parent.ID == state.stack[-2].node_id),
                                       "RIGHT"),
                                      ((e for e in outgoing if
                                        e.child.ID == state.stack[-2].node_id),
                                       "LEFT")):
                    for edge in edges:
                        self.edges_remaining.remove(edge)
                        return Action(prefix + ("-REMOTE" if edge.attrib.get("remote") else "-EDGE"),
                                      edge.tag)

                # check if a swap is necessary, and how far (if compound swap is enabled)
                swap_distance = 0
                while len(state.stack) > swap_distance + 1 and \
                        (Config().compound_swap or swap_distance < 1) and \
                        related.issubset(s.node_id for s in state.stack[:-swap_distance-2]):
                    swap_distance += 1
                if swap_distance:
                    return SWAP(swap_distance if Config().compound_swap else None)

            # check for unary edges
            for edges, action, attr in (((e for e in incoming if
                                          e.parent.ID in self.nodes_remaining and not e.attrib.get("remote")),
                                         NODE, "parent"),
                                        ((e for e in outgoing if
                                          e.child.attrib.get("implicit")),
                                         IMPLICIT, "child")):
                for edge in edges:
                    self.edges_remaining.remove(edge)
                    node = getattr(edge, attr)
                    self.nodes_remaining.remove(node.ID)
                    return action(edge.tag, node)

        if not state.buffer:
            raise Exception("No action is possible\n" + state.str("\n") + "\n" + self.str("\n"))

        return SHIFT

    def str(self, sep):
        return "nodes left: [%s]%sedges left: [%s]" % (" ".join(self.nodes_remaining), sep,
                                                       " ".join(map(str, self.edges_remaining)))

    def __str__(self):
        return str(" ")
