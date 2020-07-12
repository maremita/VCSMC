"""
An implementation of the Combinatorial Sequential Monte Carlo algorithm
for Phylogenetic Inference
"""

import numpy as np
import random
from scipy.stats import lognorm
import scipy.linalg as spl
from scipy.linalg import expm
import matplotlib.pyplot as plt
import pdb
from copy import deepcopy
from functools import reduce
from gusfield import Phylogeny
import operator as op
import networkx as nx
from networkx.drawing.nx_agraph import graphviz_layout


class Node:
    """
    Defines a Node object for visualizing phylogeny
    """

    def __init__(self, data):
        # Subedges must be a list of edges (or empty list)
        self.data = data
        self.subnodes = []
        self.parent = None
        self.edge_before = ''

    def del_subnodes(self, d):
        for n in self.subnodes:
            if n.data == d:
                self.subnodes.remove(n)

    def print_node(self):
        s = 'Node ' + self.data + ' with subnodes: '
        for n in self.subnodes:
            s += n.data + ' '
        s += '; edge before is ' + self.edge_before
        print(s)

class Vertex:

    def __init__(self, id=None, data=None):
        self.id = id
        self.data = data
        self.left = None
        self.right = None
        self.left_branch = None
        self.right_branch = None




class Graph:
    """
    Defines a Graph object for visualizing phylogeny
    """

    def __init__(self):
        self.node_dict = {}
        self.num_nodes = 0

    def add_node(self, node):
        self.num_nodes += 1
        self.node_dict[node.data] = node
        return node

    def get_node(self, data):
        if data in self.node_dict:
            return self.node_dict[data]
        else:
            return None

    def del_node(self, data):
        try:
            node_to_del = self.get_node(data)
            for d in self.node_dict:
                node = self.get_node(d)
                node.del_subnodes(data)
            del self.node_dict[data]
            self.num_nodes -= 1
            return None
        except KeyError:
            raise Exception("Node %s does not exist" % data)

    def contains(self, data):
        return data in self.node_dict

    def get_nodes(self):
        return list(self.node_dict.values())

    def get_nodes_data(self):
        return list(self.node_dict.keys())

    # For visualizations
    def build_nx_graph(self):
        # Add nodes and edges to build graph
        G_nx = nx.DiGraph()
        for key in self.get_nodes_data():
            G_nx.add_node(key)
        for node in self.get_nodes():
            for subnode in node.subnodes:
                G_nx.add_edge(node.data, subnode.data)
        return G_nx

    def draw(self, prob):
        # plotting tools. Not used here.
        G_nx = self.build_nx_graph()
        plt.figure(figsize=(10,10))
        pos = nx.kamada_kawai_layout(G_nx)
        nx.draw_networkx(G_nx, pos=pos, with_labels=True, fontsize=4,width=3.8,node_color='r',edge_color='brown')
        plt.title("Sampled Geneaology", fontsize=14)
        plt.xlabel("Prob %1.5f " % prob)
        plt.show()

    def __iter__(self):
        return iter(self.node_dict.values())



class CSMC:
    """
    CSMC takes as input a dictionary (datadict) with two keys:
     taxa: a list of n strings denoting taxa
     genome_NxSxA: a 3 tensor of genomes for the n taxa one hot encoded
    """

    def __init__(self, datadict):
        self.n = len(datadict['taxa'])
        self.taxa = datadict['taxa']
        self.genome_NxSxA = datadict['genome']
        self.Qmatrix = np.array([[-3.,1.,1.,1.],
                                 [1.,-3.,1.,1.],
                                 [1.,1.,-3.,1.],
                                 [1.,1.,1.,-3.]])
        self.Pmatrix = spl.expm(self.Qmatrix)
        self.prior = np.ones(self.Qmatrix.shape[0])/self.Qmatrix.shape[0]

    #@staticmethod
    def ncr(self, n, r):
        # Compute combinatorial term n choose r
        r = min(r, n-r)
        numer = reduce(op.mul, range(n, n-r, -1), 1)
        denom = reduce(op.mul, range(1, r+1), 1)
        return numer/denom

    #@staticmethod
    def prod(self, lst):
        output = 1
        for i in lst:
            output *= i
        return output

    #@staticmethod
    def create_node_sampler(self, jump_chain, i):
        """
        This defines the sample space of nodes at which coalescent events can happen.
        The sample space is defined in the list variable 'result'
        REVISIT ME. This is no longer necessary...
        """
        result = []
        for key in jump_chain[i]:
            if len(jump_chain[i][key]) > 1:
                for p in range(len(jump_chain[i][key])):
                    result.append(key)
        return result

    def build_tree(self, particle1, particle2, particle_coalesced, j):
        """
        Updates two lists graph_repn_data_K and graph_repn_nodes_K
        that are need to plot a phylogeny using the Graph object
        """
        #pdb.set_trace()
        n3 = Node(particle_coalesced)
        if particle1 not in self.graph_repn_data_K[j]:
            n1 = Node(particle1)
            self.graph_repn_data_K[j].append(particle1)
            self.graph_repn_nodes_K[j].append(n1)
        else:
            n1 = self.graph_repn_nodes_K[j][self.graph_repn_data_K[j].index(particle1)]
        n1.parent = n3
        n3.subnodes.append(n1)
        if particle2 not in self.graph_repn_data_K[j]:
            n2 = Node(particle2)
            self.graph_repn_data_K[j].append(particle2)
            self.graph_repn_nodes_K[j].append(n2)
        else:
            n2 = self.graph_repn_nodes_K[j][self.graph_repn_data_K[j].index(particle2)]
        n2.parent = n3
        n3.subnodes.append(n2)
        self.graph_repn_data_K[j].append(particle_coalesced)
        self.graph_repn_nodes_K[j].append(n3)


    def build_graph(self, G, master_node):
        """
        Constructs a graph for visualizing phylogeny
        """
        if master_node.subnodes == []:
            G.add_node(master_node)
            return G

        G.add_node(master_node)

        for n in master_node.subnodes:
            G = self.build_graph(G, n)

        return G

    #@staticmethod
    def resample(self, weights, jump_chain_K, i):
        """
        Resamples partial states (particles) based on importance weights
        """
        #pdb.set_trace()
        K = weights.shape[0]
        importance_weights = np.exp(weights[:,i])
        norm = np.sum(importance_weights)
        indices = np.random.choice(K, K, p = importance_weights/norm, replace=True)
        jump_chain_K = jump_chain_K[indices]
        return jump_chain_K

    def extend_partial_state(self, jump_chain_KxN, j, i):
        """
        Forms a partial state by sampling two nodes from the proposal distribution
        """
        # Set c[n-1] = c[n], copying our dictionary representing the particle set
        jump_chain_KxN[j, i + 1] = deepcopy(jump_chain_KxN[j, i])

        # Sample two posets
        sample = random.sample(jump_chain_KxN[j,i][0], 2)
        q2 = 1 / self.ncr(len(jump_chain_KxN[j,i][0]), 2)
        particle1 = sample[0]
        particle2 = sample[1]
        particle_coalesced = particle1 + '+' + particle2
        jump_chain_KxN[j,i+1][0].remove(particle1)
        jump_chain_KxN[j,i+1][0].remove(particle2)
        jump_chain_KxN[j,i+1][0].append(particle_coalesced)

        # Sample from branch length proposal
        bl1, bl2 = lognorm.rvs(1,1,1,2)

        return particle1, particle2, particle_coalesced, bl1, bl2, q2, jump_chain_KxN

    def get_internal_nodes(self,root):
        """
        Collects all internal nodes (ancestral or latent variables) for likelihood computation
        May not be necessary...
        """
        #pdb.set_trace()
        q = []
        q.append(root)
        nodes = []

        while (len(q)):

            curr = q[0]
            q.pop(0)

            isInternal = 0

            if (curr.left):
                isInternal = 1
                q.append(curr.left)

            if (curr.right):
                isInternal = 1
                q.append(curr.right)

            if (isInternal):
                nodes.append(curr)
                # print(curr.id, end = " ")

        return nodes[::-1]

    def pass_messages(self,internal_nodes):
        #pdb.set_trace()
        # Pass messages from leaf nodes to root
        #for node in self.internal_nodes:
        for node in internal_nodes:
            node.data = self.conditional_likelihood(node.left, node.right, node.left_branch, node.right_branch)

    def conditional_likelihood(self, left, right, left_branch, right_branch):
        #pdb.set_trace()
        # Computes the conditional likelihood using the formula above
        likelihood = np.zeros((len(self.genome_NxSxA[0]), self.Qmatrix.shape[0]))
        # Matrix exponentiation here is probably inefficient
        left_Pmatrix = spl.expm(self.Qmatrix * left_branch)
        right_Pmatrix = spl.expm(self.Qmatrix * right_branch)
        for i in range(self.Qmatrix.shape[0]):
            left_prob = np.dot(left_Pmatrix[i], left.data.T).T
            right_prob = np.dot(right_Pmatrix[i], right.data.T).T
            likelihood[:,i] = np.multiply(left_prob,right_prob)
        return likelihood

    def compute_tree_likelihood(self, prior, root):
        '''
        multiply probabilities over all the sites through prod
        '''
        tree_likelihood = self.prod(np.dot(prior, root.data.T))
        #self.tree_likelihood = tree_likelihood
        return tree_likelihood


    def compute_log_conditional_likelihood(self, vertex_dict, particle1, particle2, particle_coalesced, bl1, bl2):
        """
        """
        #pdb.set_trace()
        loglik = 0
        root = vertex_dict[particle_coalesced]
        internal_nodes = self.get_internal_nodes(root)

        #for i in range(self.genome_NxSxA.shape[1]):
            #BP = BeliefPropagation(self.Qmatrix, root)
            # root.left.data = self.genome_NxSxA[self.taxa.index(particle1),i]
            # root.right.data = self.genome_NxSxA[self.taxa.index(particle2),i]
        self.pass_messages(internal_nodes)
        tree_likelihood = self.compute_tree_likelihood(self.prior, root)
        loglik += np.log(tree_likelihood)

        return loglik

    def sample_phylogenies(self, K, resampling=False, showing=True):

        self.K = K
        # Represent a single jump_chain as a list of dictionaries
        jump_chain = [{} for i in range(self.n)]
        jump_chain[0][0] = self.taxa
        jump_chain_KxN = np.array([jump_chain] * K) # KxN matrix of jump_chain dictionaries

        q = 1
        log_weights_KxNm1 = np.zeros([K, self.n-1])
        log_weights_KxNm1[:,0] = np.log(1./K)

        # These need better names that describe what is happening here
        self.graph_repn_data_K = [[] for i in range(K)]
        self.graph_repn_nodes_K = [[] for i in range(K)]

        log_likelihood = np.zeros([K,self.n-1])

        vertex_dicts = [{} for k in range(K)]
        for j in range(K):
            for i in range(self.n):
                vertex_dicts[j][self.taxa[i]] = Vertex(id=self.taxa[i],data=self.genome_NxSxA[i])

        # Iterate over coalescent events
        for i in range(self.n - 1):

            # Resampling step
            if resampling and i > 0:
                jump_chain_KxN[:,i-1] = self.resample(log_weights_KxNm1, jump_chain_KxN[:,i-1], i-1)

            # Iterate over partial states
            for j in range(K):

                # Extend partial states
                particle1, particle2, particle_coalesced, bl1, bl2, q2, jump_chain_KxN \
                    = self.extend_partial_state(jump_chain_KxN, j, i)

                # Save partial set and branch length data
                vertex_dicts[j][particle_coalesced] = Vertex(id=particle_coalesced, data=None)
                vertex_dicts[j][particle_coalesced].left = vertex_dicts[j][particle1]
                vertex_dicts[j][particle_coalesced].right = vertex_dicts[j][particle2]
                vertex_dicts[j][particle_coalesced].left_branch = bl1
                vertex_dicts[j][particle_coalesced].right_branch = bl2

                # Compute log conditional likelihood across genome for a partial state
                log_likelihood[j,i] \
                    = self.compute_log_conditional_likelihood(vertex_dicts[j], particle1, particle2, particle_coalesced, bl1, bl2)

                # Compute the log importance weights
                if i > 0:
                    log_weights_KxNm1[j, i] = log_likelihood[j,i] - log_likelihood[j,i-1] - np.log(q2)

                # Build tree
                self.build_tree(particle1, particle2, particle_coalesced, j)

        # Compute importance weights across all ranks:
        #tree_probabilities = np.exp(np.sum(log_weights_KxNm1, axis=1))
        tree_log_probabilities = np.sum(log_weights_KxNm1, axis=1)
        print(tree_log_probabilities)

        # Revisit!
        # pdb.set_trace()
        if showing:
            for k in range(K):
                G = self.build_graph(Graph(), self.graph_repn_nodes_K[k][-1])
                # if showing:
                G.draw(tree_log_probabilities[k])

        else:
            G = -1

        return jump_chain_KxN, log_weights_KxNm1, G, tree_log_probabilities


if __name__ == "__main__":

    simulate_data = False
    load_strings = True

    alphabet = 'ACTG'
    alphabet_dir = {'A': [1, 0, 0, 0],
                    'C': [0, 1, 0, 0],
                    'T': [0, 0, 1, 0],
                    'G': [0, 0, 0, 1]}

    alphabet = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])

    genome_strings = ['ACTTTGAGAG','ACTTTGACAG','ACTTTGACTG','ACTTTGACTC']

    def simulateDNA(nsamples, seqlength, alphabet):
        genomes_NxSxA = np.zeros([nsamples, seqlength, alphabet.shape[0]])
        for n in range(nsamples):
            genomes_NxSxA[n] = np.array([random.choice(alphabet) for i in range(seqlength)])
        return genomes_NxSxA

    def form_dataset_from_strings(genome_strings, alphabet_dir):
        #pdb.set_trace()
        genomes_NxSxA = np.zeros([len(genome_strings),len(genome_strings[0]),len(alphabet_dir)])
        for i in range(genomes_NxSxA.shape[0]):
            for j in range(genomes_NxSxA.shape[1]):
                genomes_NxSxA[i,j] = alphabet_dir[genome_strings[i][j]]

        taxa = ['S' + str(i) for i in range(genomes_NxSxA.shape[0])]

        datadict = {'taxa': taxa,
                    'genome': genomes_NxSxA}
        return datadict


    if simulate_data:
        data_NxSxA = simulateDNA(4, 10, alphabet)
        print("Simulated genomes:\n", data_NxSxA)

        taxa = ['S' + str(i) for i in range(data_NxSxA.shape[0])]
        #print(taxa)

        datadict = {'taxa': taxa,
                    'genome': data_NxSxA}

    if load_strings:

        datadict = form_dataset_from_strings(genome_strings, alphabet_dir)

    csmc = CSMC(datadict)
    chain, log_weights, G, tree_probs = csmc.sample_phylogenies(100, resampling=True)