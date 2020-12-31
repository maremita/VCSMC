"""
An implementation of the Variational Combinatorial Sequential Monte Carlo for Bayesian Phylogenetic Inference.
  Combinatorial Sequential Monte Carlo is used to form a variational objective
  to simultaneously learn the parameters of the proposal and target distribution
  and perform Bayesian phylogenetic inference.
"""

import numpy as np
import tensorflow.compat.v1 as tf
import tensorflow_probability as tfp
import matplotlib.pyplot as plt
import pdb
from datetime import datetime
import os
import pickle

# @staticmethod
def ncr(n, r):
    # Compute combinatorial term n choose r
    numer = tf.reduce_prod(tf.range(n-r+1, n+1))
    denom = tf.reduce_prod(tf.range(1, r+1))
    return numer / denom

# @staticmethod
def double_factorial(n):
    # Compute double factorial: n!!
    return tf.reduce_prod(tf.range(n, 0, -2))


class VCSMC:
    """
    VCSMC takes as input a dictionary (datadict) with two keys:
     taxa: a list of n strings denoting taxa
     genome_NxSxA: a 3 tensor of genomes for the n taxa one hot encoded
    """

    def __init__(self, datadict, K):
        self.taxa = datadict['taxa']
        self.genome_NxSxA = datadict['genome']
        self.K = K
        self.N = len(self.genome_NxSxA)
        self.S = len(self.genome_NxSxA[0])
        self.A = len(self.genome_NxSxA[0, 0])
        self.y_q = tf.linalg.set_diag(tf.Variable(np.zeros((self.A, self.A)) + 1/self.A, dtype=tf.float64, name='Qmatrix'), [0]*self.A)
        self.y_station = tf.Variable(np.zeros(self.A) + 1 / self.A, dtype=tf.float64, name='stationary_probs')
        self.left_branches_param = tf.Variable(np.zeros(self.N-1)+10, dtype=tf.float64, name='left_branches_param')
        self.right_branches_param = tf.Variable(np.zeros(self.N-1)+10, dtype=tf.float64, name='right_branches_param')
        self.stationary_probs = self.get_stationary_probs()
        self.Qmatrix = self.get_Q()
        self.learning_rate = tf.placeholder(dtype=tf.float64, shape=[])

    def get_stationary_probs(self):
        """ Compute stationary probabilities of the Q matrix """
        denom = tf.reduce_sum(tf.exp(self.y_station))

        return tf.expand_dims(tf.exp(self.y_station) / denom, axis=0)

    def get_Q(self):
        """
        Forms the transition matrix of the continuous time Markov Chain, constraints
        are satisfied by defining off-diagonal terms using the softmax function
        """
        denom = tf.reduce_sum(tf.linalg.set_diag(tf.exp(self.y_q), [0]*self.A), axis=1)
        denom = tf.stack([denom]*self.A, axis=1)
        q_entry = tf.multiply(tf.linalg.set_diag(tf.exp(self.y_q), [0]*self.A), 1/denom)
        hyphens = tf.reduce_sum(q_entry, axis=1)
        Q = tf.linalg.set_diag(q_entry, -hyphens)

        return Q

    def resample(self, core, leafnode_record, JC_K, log_weights):
        """
        Resample partial states by drawing from a categorical distribution whose parameters are normalized importance weights
        JumpChain (JC_K) is a tensor formed from a numpy array of lists of strings, returns a resampled JumpChain tensor
        """
        log_normalized_weights = log_weights - tf.reduce_logsumexp(log_weights)
        indices = tf.squeeze(tf.random.categorical(tf.expand_dims(log_normalized_weights, axis=0), self.K))
        resampled_core = tf.gather(core, indices)
        resampled_record = tf.gather(leafnode_record, indices)
        resampled_JC_K = tf.gather(JC_K, indices)

        return resampled_core, resampled_record, resampled_JC_K, indices

    def extend_partial_state(self, JCK, r):
        """
        Extends partial state by sampling two states to coalesce (Gumbel-max trick to sample without replacement)
        JumpChain (JC_K) is a tensor formed from a numpy array of lists of strings, returns a new JumpChain tensor
        """
        # Compute combinatorial term
        # pdb.set_trace()
        q = 1 / ncr(self.N - r, 2)
        data = tf.reshape(tf.range((self.N - r) * self.K), (self.K, self.N - r))
        data = tf.mod(data, (self.N - r))
        data = tf.cast(data, dtype=tf.float32)
        # Gumbel-max trick to sample without replacement
        z = -tf.math.log(-tf.math.log(tf.random.uniform(tf.shape(data), 0, 1)))
        top_values, coalesced_indices = tf.nn.top_k(data + z, 2)
        bottom_values, remaining_indices = tf.nn.top_k(tf.negative(data + z), self.N - r - 2)
        JC_keep = tf.gather(tf.reshape(JCK, [self.K * (self.N - r)]), remaining_indices)
        particles = tf.gather(tf.reshape(JCK, [self.K * (self.N - r)]), coalesced_indices)
        particle1 = particles[:, 0]
        particle2 = particles[:, 1]
        # Form new state
        particle_coalesced = particle1 + '+' + particle2
        # Form new Jump Chain
        JCK = tf.concat([JC_keep, tf.expand_dims(particle_coalesced, axis=1)], axis=1)

        return particle1, particle2, particle_coalesced, coalesced_indices, remaining_indices, q, JCK

    def conditional_likelihood(self, l_data, r_data, l_branch, r_branch):
        """
        Computes conditional complete likelihood at an ancestor node
        by passing messages from left and right children
        """
        left_Pmatrix = tf.linalg.expm(self.Qmatrix * l_branch)
        right_Pmatrix = tf.linalg.expm(self.Qmatrix * r_branch)
        left_prob = tf.matmul(l_data, left_Pmatrix)
        right_prob = tf.matmul(r_data, right_Pmatrix)
        likelihood = tf.multiply(left_prob, right_prob)

        return likelihood

    def compute_tree_likelihood(self, data):
        """
        Forms a log probability measure by dotting the stationary probs with tree likelihood
        """
        tree_likelihood = tf.matmul(self.stationary_probs, data, transpose_b=True)
        tree_loglik = tf.reduce_sum(tf.log(tree_likelihood))

        return tree_loglik 

    def compute_log_ZSMC(self, log_weights):
        """
        Forms the estimator log_ZSMC, a multi sample lower bound to the likelihood
        Z_SMC is formed by averaging over weights and multiplying over coalescent events
        """
        log_Z_SMC = tf.reduce_sum(tf.reduce_logsumexp(log_weights - tf.log(tf.cast(self.K, tf.float64)), axis=1))

        return log_Z_SMC

    def body_update_data(self, new_data, new_record, core, new_core, leafnode_record, new_leafnode_record, 
        q_l_branch_samples, q_r_branch_samples, coalesced_indices, remaining_indices, r, k):
        """
        Update the core tensor representing the distribution over characters for the ancestral taxa,
        coalesced indices and branch lengths are also indexed in order to compute the conditional likelihood
        and pass messages from children to parent node.
        """
        n1 = tf.gather_nd(coalesced_indices, [k, 0])
        n2 = tf.gather_nd(coalesced_indices, [k, 1])
        l_data = tf.squeeze(tf.gather_nd(core, [[k, n1]]))
        r_data = tf.squeeze(tf.gather_nd(core, [[k, n2]]))
        l_branch = tf.gather(q_l_branch_samples, k)
        r_branch = tf.gather(q_r_branch_samples, k)
        mtx = self.conditional_likelihood(l_data, r_data, l_branch, r_branch)
        mtx_ext = tf.expand_dims(tf.expand_dims(mtx, axis=0), axis=0)  # 1x1xSxA
        new_data = tf.concat([new_data, mtx_ext], axis=0)  # kx1xSxA

        leafnode_num = tf.squeeze(tf.gather_nd(leafnode_record, [[k, n1]])) + \
                       tf.squeeze(tf.gather_nd(leafnode_record, [[k, n2]]))
        new_record = tf.concat([new_record, [[leafnode_num]]], axis=0)

        remaining_indices_k = tf.gather(remaining_indices, k)
        core_remaining_indices = tf.gather(tf.gather(core, k), remaining_indices_k)
        new_core = tf.concat([new_core, [core_remaining_indices]], axis=0)
        record_remaining_indices = tf.gather(tf.gather(leafnode_record, k), remaining_indices_k)
        new_leafnode_record = tf.concat([new_leafnode_record, [record_remaining_indices]], axis=0)

        k = k + 1

        return new_data, new_record, core, new_core, leafnode_record, new_leafnode_record, \
        q_l_branch_samples, q_r_branch_samples, coalesced_indices, remaining_indices, r, k

    def cond_update_data(self, new_data, new_record, core, new_core, leafnode_record, new_leafnode_record, 
        q_l_branch_samples, q_r_branch_samples, coalesced_indices, remaining_indices, r, k):
        return k < self.K

    def body_update_weights(self, log_weights_r, log_likelihood_r, log_likelihood_tilda, coalesced_indices, 
        core, leafnode_record, left_branches, right_branches, left_branches_param_r, right_branches_param_r, q, r, k):
        data = tf.squeeze(tf.gather_nd(core, [[k, self.N-r-2]]))
        log_likelihood_r_k = self.compute_tree_likelihood(data)
        log_likelihood_r = tf.concat([log_likelihood_r, [log_likelihood_r_k]], axis=0)
        log_weights_r = tf.concat([log_weights_r, [log_likelihood_r_k]], axis=0)
        k = k + 1

        return log_weights_r, log_likelihood_r, log_likelihood_tilda, coalesced_indices, \
        core, leafnode_record, left_branches, right_branches, left_branches_param_r, right_branches_param_r, q, r, k

    def cond_update_weights(self, log_weights_r, log_likelihood_r, log_likelihood_tilda, coalesced_indices, 
        core, leafnode_record, left_branches, left_branches_param_r, right_branches_param_r, right_branches, q, r, k):
        return k < self.K

    def cond_true_resample(self, log_likelihood_tilda, core, leafnode_record, log_weights, log_likelihood, jump_chains, jump_chain_tensor, r):
        core, leafnode_record, jump_chain_tensor, indices = self.resample(core, leafnode_record, jump_chain_tensor, tf.gather(log_weights, r))
        log_likelihood_tilda = tf.gather_nd(tf.gather(tf.transpose(log_likelihood), indices), [[k, r] for k in range(self.K)])
        jump_chains = tf.concat([jump_chains, jump_chain_tensor], axis=1)
        
        return log_likelihood_tilda, core, leafnode_record, jump_chains, jump_chain_tensor

    def cond_false_resample(self, log_likelihood_tilda, core, leafnode_record, log_weights, log_likelihood, jump_chains, jump_chain_tensor, r):
        return log_likelihood_tilda, core, leafnode_record, jump_chains, jump_chain_tensor

    def rank_update_body_main(self, log_weights, log_likelihood, log_likelihood_tilda, jump_chains, jump_chain_tensor, 
        core, leafnode_record, left_branches, right_branches, r):
        """
        Define tensors for log_weights, log_likelihood, jump_chain_tensor and core (state data for distribution over characters for ancestral taxa)
        by iterating over rank events.
        """
        # Resample
        log_likelihood_tilda, core, leafnode_record, jump_chains, jump_chain_tensor = tf.cond(r > 0,
            lambda: self.cond_true_resample(log_likelihood_tilda, core, leafnode_record, log_weights, log_likelihood, jump_chains, jump_chain_tensor, r),
            lambda: self.cond_false_resample(log_likelihood_tilda, core, leafnode_record, log_weights, log_likelihood, jump_chains, jump_chain_tensor, r))

        # Extend partial states
        particle1, particle2, particle_coalesced, coalesced_indices, remaining_indices, \
        q, jump_chain_tensor = self.extend_partial_state(jump_chain_tensor, r)

        # Branch lengths
        left_branches_param_r = tf.gather(self.left_branches_param, r)
        right_branches_param_r = tf.gather(self.right_branches_param, r)
        q_l_branch_dist = tfp.distributions.Exponential(rate=left_branches_param_r)
        q_r_branch_dist = tfp.distributions.Exponential(rate=right_branches_param_r)
        q_l_branch_samples = q_l_branch_dist.sample(self.K) 
        q_r_branch_samples = q_r_branch_dist.sample(self.K) 
        left_branches = tf.concat([left_branches, [q_l_branch_samples]], axis=0) 
        right_branches = tf.concat([right_branches, [q_r_branch_samples]], axis=0) 

        # Update partial set data
        new_data = tf.constant(np.zeros((1, 1, self.S, self.A))) # to be used in tf.while_loop
        new_record = tf.constant(np.zeros((1, 1)), dtype=tf.int32) # to be used in tf.while_loop
        remaining_indices_k = tf.gather(remaining_indices, 0)
        core_remaining_indices = tf.gather(tf.gather(core, 0), remaining_indices_k)
        new_core = tf.expand_dims(core_remaining_indices, axis=0)
        record_remaining_indices = tf.gather(tf.gather(leafnode_record, 0), remaining_indices_k)
        new_leafnode_record = tf.expand_dims(record_remaining_indices, axis=0)
        new_data, new_record, core, new_core, leafnode_record, new_leafnode_record, lb_, rb_, coalesced_indices, remaining_indices, i, k = tf.while_loop(
                                                       self.cond_update_data, self.body_update_data,
                                                       loop_vars=[new_data, new_record, core, new_core, leafnode_record, new_leafnode_record, 
                                                                  q_l_branch_samples, q_r_branch_samples, coalesced_indices, remaining_indices, r, tf.constant(0)],
                                                       shape_invariants=[tf.TensorShape([None, 1, self.S, self.A]),
                                                                         tf.TensorShape([None, 1]), core.get_shape(),
                                                                         tf.TensorShape([None, None, self.S, self.A]),
                                                                         leafnode_record.get_shape(), tf.TensorShape([None, None]),
                                                                         q_l_branch_samples.get_shape(), q_r_branch_samples.get_shape(), 
                                                                         coalesced_indices.get_shape(), remaining_indices.get_shape(),
                                                                         tf.TensorShape([]), tf.TensorShape([])])
        new_data = tf.gather(new_data, tf.range(1, self.K + 1)) # remove the trivial index 0
        new_record = tf.gather(new_record, tf.range(1, self.K + 1)) # remove the trivial index 0
        core = tf.gather(new_core, tf.range(1, self.K + 1)) # remove the trivial index 0
        core = tf.concat([core, new_data], axis=1)
        leafnode_record = tf.gather(new_leafnode_record, tf.range(1, self.K + 1)) # remove the trivial index 0
        leafnode_record = tf.concat([leafnode_record, new_record], axis=1)

        # Comptue weights
        log_weights_r, log_likelihood_r, log_likelihood_tilda, coalesced_indices, core_, leafnode_record_, lb_, rb_, lbp_, rbp_, q, i, k = \
            tf.while_loop(self.cond_update_weights, self.body_update_weights,
                          loop_vars=[tf.constant(np.zeros(1)), tf.constant(np.zeros(1)), log_likelihood_tilda, coalesced_indices, 
                                     core, leafnode_record, left_branches, right_branches, left_branches_param_r, right_branches_param_r,
                                     q, r, tf.constant(0)],
                          shape_invariants=[tf.TensorShape([None]), tf.TensorShape([None]),
                                            log_likelihood_tilda.get_shape(), coalesced_indices.get_shape(),
                                            core.get_shape(), leafnode_record.get_shape(), 
                                            left_branches.get_shape(), right_branches.get_shape(), tf.TensorShape([]), tf.TensorShape([]), 
                                            tf.TensorShape([]), tf.TensorShape([]), tf.TensorShape([])])
        log_weights = tf.concat([log_weights, [tf.gather(log_weights_r, tf.range(1, self.K + 1))]], axis=0)
        log_likelihood = tf.concat([log_likelihood, [tf.gather(log_likelihood_r, tf.range(1, self.K + 1))]], axis=0) # pi(t) = pi(Y|t, b, theta) * pi(t, b|theta) / pi(Y)
        r = r + 1

        return log_weights, log_likelihood, log_likelihood_tilda, jump_chains, jump_chain_tensor, \
        core, leafnode_record, left_branches, right_branches, r

    def rank_update_cond_main(self, log_weights, log_likelihood, log_likelihood_tilda, jump_chains, jump_chain_tensor, 
        core, leafnode_record, left_branches, right_branches, r):
        return r < self.N - 1

    def sample_phylogenies(self):
        """
        Main sampling routine that performs combinatorial SMC by calling the rank update subroutine
        """
        N = self.N
        S = self.S
        A = self.A
        K = self.K

        self.core = tf.placeholder(dtype=tf.float64, shape=(K, N, S, A))
        leafnode_record = tf.constant(1, shape=(K, N), dtype=tf.int32) # Keeps track of self.core

        left_branches = tf.constant(0, shape=(1, K), dtype=tf.float64)
        right_branches = tf.constant(0, shape=(1, K), dtype=tf.float64)

        log_weights = tf.constant(0, shape=(1, K), dtype=tf.float64)
        log_likelihood = tf.constant(0, shape=(1, K), dtype=tf.float64)
        log_likelihood_tilda = tf.constant(np.zeros((K)), dtype=tf.float64)
        
        self.jump_chains = tf.constant('', shape=(K, 1))
        self.jump_chain_tensor = tf.constant([self.taxa] * K, name='JumpChainK')

        # MAIN LOOP
        log_weights, log_likelihood, log_likelihood_tilda, self.jump_chains, self.jump_chain_tensor, \
        core_final, record_final, left_branches, right_branches, r = \
            tf.while_loop(self.rank_update_cond_main, self.rank_update_body_main,
            loop_vars=[log_weights, log_likelihood, log_likelihood_tilda, self.jump_chains, self.jump_chain_tensor, 
            self.core, leafnode_record, left_branches, right_branches, tf.constant(0)],
            shape_invariants=[tf.TensorShape([None, K]), tf.TensorShape([None, K]), log_likelihood_tilda.get_shape(),
                              tf.TensorShape([K, None]), tf.TensorShape([K, None]), tf.TensorShape([K, None, S, A]),
                              tf.TensorShape([K, None]), tf.TensorShape([None, K]), tf.TensorShape([None, K]), 
                              tf.TensorShape([])])

        self.log_weights = tf.gather(log_weights, list(range(1, N))) # remove the trivial index 0
        self.log_likelihood = tf.gather(log_likelihood, list(range(1, N))) # remove the trivial index 0
        self.left_branches = tf.gather(left_branches, list(range(1, N))) # remove the trivial index 0
        self.right_branches = tf.gather(right_branches, list(range(1, N))) # remove the trivial index 0
  
        self.elbo = self.compute_log_ZSMC(log_weights)
        self.log_likelihood_R = tf.gather(log_likelihood, N-2)
        self.cost = - self.elbo
        self.log_likelihood_tilda = log_likelihood_tilda

        return self.elbo

    def train(self, numIters=800, memory_optimization='on'):
        """
        Run the train op in a TensorFlow session and evaluate variables
        """
        K = self.K
        self.lr = 0.001

        config = tf.ConfigProto()
        if memory_optimization == 'off':
            from tensorflow.core.protobuf import rewriter_config_pb2
            off = rewriter_config_pb2.RewriterConfig.OFF
            config.graph_options.rewrite_options.memory_optimization = off

        self.sample_phylogenies()
        print('===================\nFinished constructing computational graph!', '\n===================')

        self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate).minimize(self.cost)
        feed_data = np.array([self.genome_NxSxA] * K, dtype=np.double)

        sess = tf.Session(config=config)
        init = tf.global_variables_initializer()
        sess.run(init)
        print('===================\nInitial evaluation of ELBO:', round(sess.run(-self.cost, feed_dict={self.core: feed_data}), 3), '\n===================')
        print(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=tf.get_variable_scope().name))
        print('Training begins --')
        elbos = []
        Qmatrices = []
        left_branches = []
        right_branches = []
        jump_chain_evolution = []
        log_weights = []
        ll = []
        ll_tilda = []
        #best_cost = -inf
        for i in range(numIters):
            bt = datetime.now()
            _, cost = sess.run([self.optimizer, self.cost], feed_dict={self.core: feed_data, self.learning_rate: self.lr})
            elbos.append(-cost)
            print('Epoch', i+1)
            print('ELBO\n', round(-cost, 3))
            Qs = sess.run(self.Qmatrix)
            lb = np.round(sess.run(self.left_branches, feed_dict={self.core: feed_data}), 3)
            rb = np.round(sess.run(self.right_branches, feed_dict={self.core: feed_data}), 3)
            log_Ws = np.round(sess.run(self.log_weights, feed_dict={self.core: feed_data}), 3)
            log_liks = sess.run(self.log_likelihood, feed_dict={self.core: feed_data})
            log_lik_tilde = sess.run(self.log_likelihood_tilda, feed_dict={self.core: feed_data})
            print('Q-matrix\n', Qs)
            print('Stationary probabilities\n', sess.run(self.stationary_probs))
            # print('Left branches (for five particles)\n', sess.run(tf.gather(self.left_branches, [0, int(K/4), int(K/2), int(3*K/4), K-1])))
            # print('Right branches (for five particles)\n', sess.run(tf.gather(self.right_branches, [0, int(K/4), int(K/2), int(3*K/4), K-1])))
            print('Left branches\n', lb)
            print('Right branches\n', rb)
            print('Log Weights\n', log_Ws)
            print('Log likelihood\n', np.round(log_liks,3))
            print('Log likelihood tilde\n', np.round(log_lik_tilde,3))
            Qmatrices.append(Qs)
            left_branches.append(lb)
            right_branches.append(rb)
            ll.append(log_liks)
            ll_tilda.append(log_lik_tilde)
            log_weights.append(log_Ws)
            #pdb.set_trace()
            jc = sess.run(self.jump_chains, feed_dict={self.core: feed_data})
            jump_chain_evolution.append(jc)
            at = datetime.now()
            print('Time spent\n', at-bt, '\n-----------------------------------------')
        print("Done training.")

        # Create local directory and save experiment results
        tm = str(datetime.now())
        local_rlt_root = './results/'
        save_dir = local_rlt_root + (tm[:10]+'-'+tm[11:13]+tm[14:16]+tm[17:19]) + '/'
        if not os.path.exists(save_dir): os.makedirs(save_dir)

        plt.imshow(sess.run(self.Qmatrix))
        plt.title("Trained Q matrix")
        plt.savefig(save_dir + "Qmatrix.png")

        plt.figure(figsize=(10,10))
        plt.plot(elbos)
        plt.ylabel("log $Z_{SMC}$")
        plt.xlabel("Epochs")
        plt.title("Elbo convergence across epochs")
        plt.savefig(save_dir + "ELBO.png")
        #plt.show()

        plt.figure(figsize=(10, 10))
        myll = np.asarray(ll)
        plt.plot(myll[:,-1,:],c='black',alpha=0.2)
        plt.plot(np.average(myll[:,-1,:],axis=1),c='yellow')
        plt.ylabel("log likelihood")
        plt.xlabel("Epochs")
        plt.title("Log likelihood convergence across epochs")
        plt.savefig(save_dir + "ll.png")
        #plt.show()

        #pdb.set_trace()
        # Save best log-likelihood value and jump chain
        best_log_lik = np.asarray(ll)[np.argmax(elbos),-1]#.shape
        print("Best log likelihood values:\n", best_log_lik)
        best_jump_chain = jump_chain_evolution[np.argmax(elbos)]

        resultDict = {'cost': np.asarray(elbos),
                      'nParticles': self.K,
                      'nTaxa': self.N,
                      'lr': self.lr,
                      'log_weights': np.asarray(log_weights),
                      'Qmatrices': np.asarray(Qmatrices),
                      'left_branches': left_branches,
                      'right_branches': right_branches,
                      'log_lik': np.asarray(ll),
                      'll_tilde': np.asarray(ll_tilda),
                      'jump_chain_evolution': jump_chain_evolution,
                      'best_epoch' : np.argmax(elbos),
                      'best_log_lik': best_log_lik,
                      'best_jump_chain': best_jump_chain}



        with open(save_dir + 'results.p', 'wb') as f:
            #pdb.set_trace()
            pickle.dump(resultDict, f)

        print("Finished...")
        #pdb.set_trace()

        return