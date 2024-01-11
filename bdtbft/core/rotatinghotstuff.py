import re
from gevent import monkey; monkey.patch_all(thread=False)

import hashlib
import json
import logging
import os
import pickle
import traceback
import gevent
import time
import numpy as np
from gevent import Greenlet
from gevent.event import Event
from gevent.queue import Queue
from collections import defaultdict, namedtuple
from enum import Enum
from merkletree import group_and_build_merkle_tree, merkleVerify, merkleTree
from bdtbft.core.hsfastpath import hsfastpath
from crypto.threshsig.boldyreva import serialize, deserialize1
from crypto.threshsig.boldyreva import TBLSPrivateKey, TBLSPublicKey
from crypto.ecdsa.ecdsa import PrivateKey
from bdtbft.exceptions import UnknownTagError
from crypto.ecdsa.ecdsa import ecdsa_sign, ecdsa_vrfy, PublicKey


def parse_shard_info(tx):
    input_shards = re.findall(r'Input Shard: (\[.*?\])', tx)[0]
    input_valids = re.findall(r'Input Valid: (\[.*?\])', tx)[0]
    output_shard = re.findall(r'Output Shard: (\d+)', tx)[0]
    output_valid = re.findall(r'Output Valid: (\d+)', tx)[0]

    input_shards = eval(input_shards)
    input_valids = eval(input_valids)
    output_shard = int(output_shard)
    output_valid = int(output_valid)

    return input_shards, input_valids, output_shard, output_valid


def hash(x):
    return hashlib.sha256(pickle.dumps(x)).digest()


class BroadcastTag(Enum):
    FAST = 'FAST'
    VIEW_CHANGE = 'VIEW_CHANGE'
    NEW_VIEW = 'NEW_VIEW'
    VOTE = 'VOTE'
    LD = 'LD'
    SIGN = 'SIGN'
    CL_M = 'CL_M'
    CL = 'CL'
    BREAK = 'BREAK'    


BroadcastReceiverQueues = namedtuple(
    'BroadcastReceiverQueues', ('FAST', 'VIEW_CHANGE', 'NEW_VIEW', 'VOTE', 'LD', 'SIGN', 'CL_M', 'CL', 'BREAK'))


def broadcast_receiver_loop(recv_func, recv_queues):
    while True:
        #gevent.sleep(0)
        sender, (tag, j, msg) = recv_func()
        if tag not in BroadcastTag.__members__:
            # TODO Post python 3 port: Add exception chaining.
            # See https://www.python.org/dev/peps/pep-3134/
            raise UnknownTagError('Unknown tag: {}! Must be one of {}.'.format(
                tag, BroadcastTag.__members__.keys()))
        recv_queue = recv_queues._asdict()[tag]

        try:
            recv_queue.put_nowait((sender, msg))
        except AttributeError as e:
            print("error", sender, (tag, j, msg))
            traceback.print_exc()


class RotatingLeaderHotstuff():
    """RotatingLeaderHotstuff object used to run the protocol

    :param str sid: The base name of the common coin that will be used to
        derive a nonce to uniquely identify the coin.
    :param int pid: Node id.
    :param int Bfast: Batch size of transactions.
    :param int Bacs: Batch size of transactions.
    :param int N: Number of nodes in the network.
    :param int f: Number of faulty nodes that can be tolerated.
    :param TBLSPublicKey sPK: Public key of the (f, N) threshold signature.
    :param TBLSPrivateKey sSK: Signing key of the (f, N) threshold signature.
    :param TBLSPublicKey sPK1: Public key of the (N-f, N) threshold signature.
    :param TBLSPrivateKey sSK1: Signing key of the (N-f, N) threshold signature.
    :param list sPK2s: Public key(s) of ECDSA signature for all N parties.
    :param PrivateKey sSK2: Signing key of ECDSA signature.
    :param str ePK: Public key of the threshold encryption.
    :param str eSK: Signing key of the threshold encryption.
    :param send:
    :param recv:
    :param K: a test parameter to specify break out after K epochs
    """

    def __init__(self, sid, shard_id, pid, S, T, Bfast, Bacs, shard_num, N, f, sPK, sSK, sPK1, sSK1, sPK2s, sSK2, ePK, eSK, send, recv, logg, K=3, mute=False, omitfast=False):

        self.SLOTS_NUM = S
        self.TIMEOUT = T
        self.FAST_BATCH_SIZE = Bfast
        self.FALLBACK_BATCH_SIZE = Bacs

        self.sid = sid
        self.shard_id = shard_id
        self.id = pid
        self.shard_num = shard_num
        self.N = N
        self.f = f
        self.sPK = sPK
        self.sSK = sSK
        self.sPK1 = sPK1
        self.sSK1 = sSK1
        self.sPK2s = sPK2s
        self.sSK2 = sSK2
        self.ePK = ePK
        self.eSK = eSK
        self._send = send
        self._recv = recv
        self.logger = logg
        self.epoch = 0  # Current block number
        self.transaction_buffer = Queue()
        self._per_epoch_recv = {}  # Buffer of incoming messages

        self.K = K
        self.pool = defaultdict(list)

        self.s_time = 0
        self.e_time = 0

        self.tx_batch = ''
        self.txcnt = 0
        self.txdelay = 0
        self.vcdelay = []

        self.mute = mute
        self.omitfast = omitfast
        self.round = 0

    def submit_tx(self, tx):
        """Appends the given transaction to the transaction buffer.

        :param tx: Transaction to append to the buffer.
        """
        # print('backlog_tx', self.id, tx)
        #if self.logger != None:
        #    self.logger.info('Backlogged tx at Node %d:' % self.id + str(tx))
        self.transaction_buffer.put_nowait(tx)

    def run_bft(self):
        """Run the Mule protocol."""

        if self.mute:
            muted_nodes = [each * 4 + 1 for each in range(int((self.N-1)/4))]
            if self.id in muted_nodes:
                #T = 0.00001
                while True:
                    time.sleep(10)

        #if self.mute:
        #    muted_nodes = [each * 3 + 1 for each in range(int((self.N-1)/3))]
        #    if self.id in muted_nodes:
        #        self._send = lambda j, o: time.sleep(100)
        #        self._recv = lambda: (time.sleep(100) for i in range(10000))
        self._stop_recv_loop = False

        def _recv_loop():
            """Receive messages."""
            while not self._stop_recv_loop:
                #gevent.sleep(0)
                try:
                    (sender, (r, msg)) = self._recv()
                    # Maintain an *unbounded* recv queue for each epoch
                    if r not in self._per_epoch_recv:
                        self._per_epoch_recv[r] = Queue()
                    # Buffer this message
                    self._per_epoch_recv[r].put_nowait((sender, msg))
                except:
                    continue

        #self._recv_thread = gevent.spawn(_recv_loop)
        self._recv_thread = Greenlet(_recv_loop)
        self._recv_thread.start()

        self.s_time = time.time()
        #print("shard: ", self.shard_id, "node: ", self.id, " round: ", self.round, " starts Hotstuff BFT consensus")


        # For each epoch
        e = self.epoch

        if e not in self._per_epoch_recv:
            self._per_epoch_recv[e] = Queue()

        # Select B transactions (TODO: actual random selection)
        tx_to_send = []
        for _ in range(self.FAST_BATCH_SIZE):
            tx_to_send.append(self.transaction_buffer.get_nowait())


        '''TXs = read_pkl_file(self.TXs)
        TXs = [tx for tx in TXs if tx not in tx_to_send]
        write_pkl_file(TXs, self.TXs)'''

        def make_epoch_send(e):
            def _send(j, o):
                self._send(j, (e, o))
            def _send_1(j, o):
                self._send(j, (e+1, o))
            return _send, _send_1

        send_e, _send_e_1 = make_epoch_send(e)
        recv_e = self._per_epoch_recv[e].get

        self._run_epoch(e, tx_to_send, send_e, _send_e_1, recv_e)

        # print('new block at %d:' % self.id, new_tx)
        #if self.logger != None:
        #    self.logger.info('Node %d Delivers Block %d: ' % (self.id, self.epoch) + str(new_tx))

        # print('buffer at %d:' % self.id, self.transaction_buffer)
        #if self.logger != None:
        #    self.logger.info('Backlog Buffer at Node %d:' % self.id + str(self.transaction_buffer))

        self.e_time = time.time()
        if self.logger != None:
            self.logger.info("node %d breaks in %f seconds with total delivered Txs %d and average delay %f" % (self.id, self.e_time-self.s_time, self.txcnt, self.txdelay) )
        else:
            print("node %d breaks in %f seconds with total delivered Txs %d and average delay %f" % (self.id, self.e_time-self.s_time, self.txcnt, self.txdelay))
       
        self._stop_recv_loop = True
        self._recv_thread.kill()
        self.round += 1  # Increment the round


        #gevent.sleep(0)

    def _recovery(self):
        # TODO: here to implement to recover blocks
        pass

    #
    def _run_epoch(self, e, tx_to_send, send, send_1, recv):
        """Run one protocol epoch.

        :param int e: epoch id
        :param send:
        :param recv:
        """

        sid = self.sid
        pid = self.id
        N = self.N
        f = self.f
        leader = e % N

        T = self.TIMEOUT
        break_count = 0
        #if e == 0:
        #    T = 15
        #if self.mute:
        #    muted_nodes = [each * 3 + 1 for each in range(int((N-1)/3))]
        #    if leader in muted_nodes:
        #        T = 0.00001

        #S = self.SLOTS_NUM
        #T = self.TIMEOUT
        #B = self.FAST_BATCH_SIZE

        epoch_id = sid + 'FAST' + str(e)
        hash_genesis = hash(epoch_id)

        fast_recv = Queue()  # The thread-safe queue to receive the messages sent to fast_path of this epoch
        viewchange_recv = Queue()
        newview_recv = Queue()
        vote_recv = Queue()
        ld_recv = Queue()
        sign_recv = Queue()
        clm_recv = Queue()
        cl_recv = Queue()
        break_recv = Queue()

        recv_queues = BroadcastReceiverQueues(
            FAST=fast_recv,
            VIEW_CHANGE=viewchange_recv,
            NEW_VIEW=newview_recv,
            VOTE=vote_recv,
            LD=ld_recv,
            SIGN=sign_recv,
            CL_M=clm_recv,
            CL=cl_recv,
            BREAK=break_recv
        )
        recv_t = gevent.spawn(broadcast_receiver_loop, recv, recv_queues)

        latest_notarized_block = None

        def handle_messages_break_recv():
            nonlocal break_count
            while break_count < self.shard_num:
            #while break_count < self.N:
                (sender, msg) = break_recv.get()
                break_count+=1
                #print(f"shard {self.shard_id} node {self.id} break_count {break_count}, received from {sender}")

        def handle_messages_vote_recv():
            nonlocal voters, votes, decides
            while True:
                (sender, msg) = vote_recv.get()
                # print(f"[Vote recv] node {self.id} Received message from {sender}")
                # assert sender in range(N)
                # print("Node %d receive VOTE message from node %d" % (self.id, sender))
                if len(voters) < N - f:

                    tx_batch, rt, sig_p = msg
                    # print(rt)
                    if sender not in voters:
                        try:
                            assert ecdsa_vrfy(self.sPK2s[sender % N], rt, sig_p)
                        except AssertionError:
                            if self.logger is not None:
                                self.logger.info("Vote signature failed!")
                            continue

                        voters.add(sender)
                        votes[sender] = sig_p

                        if len(voters) == N - f:
                            Sigma = tuple(votes.items())
                            decides.put_nowait((tx_batch, rt, Sigma))
                            break

        def handle_messages_ld_recv():
            ld_cnt = 0
            while True:
                receive = False
                try:
                    receive = True
                    (sender, msg) = ld_recv.get()
                    ld_cnt += 1
                except Exception as e:
                    continue

                if receive:
                    # receive LD message from other shard
                    #print("[msg]:", type(msg), msg)
                    txs, proof, rt, shard_branch, positions = msg
                    '''print('[LD] Node %d in shard %d receive LD message from %d' % (self.id, self.shard_id,sender))'''
                    #print(tx_batch)        
                    # extract the transactions output shard of which is this shard
                    #txs = [tx for tx in json.loads(tx_batch) if parse_shard_info(tx)[2] == self.shard_id]

                    #if do not exist tx that output shard == self.shard_id
                    #if len(txs) == 0:
                    #    break

                    # construct merkle tree of these transactions
                    val = merkleTree(txs)[1]
                    index = positions[self.shard_id]
                    #print(tx_batch,txs,str(self.shard_id), val, rt, shard_branch, index)

                    try:
                        assert merkleVerify(str(self.shard_id), val, rt, shard_branch, index) == True
                        #print("Sign signature success1")
                    except AssertionError:
                        #print("Sign signature fail1")
                        if self.logger is not None:
                            self.logger.info("MerkleTree verify failed!")
                        continue

                    # add these transactions to pool
                    #(TODO: change cnt to list)
                    '''for tx in txs:
                        if tx in self.pool:
                            self.pool[tx] += 1
                        else:
                            self.pool.update({tx:1})

                    from collections import Counter

                    # find transactions that can be finished in this shard
                    matching_txs = []
                    for tx_pool in self.pool:
                        input_shards, _,_,_ = parse_shard_info(tx_pool)
                        if self.pool[tx_pool] == len(input_shards):
                            matching_txs.append(tx_pool)
                    '''
                    # record shard_id that sends tx
                    for tx in txs:
                        _, _, _, output_valid = parse_shard_info(tx)
                        if output_valid == 0:
                            self.pool[tx].append(sender // self.N)

                    matching_txs = []
                    for tx_pool in self.pool:
                        #print(tx_pool, self.pool[tx_pool])
                        input_shards, _,_,_ = parse_shard_info(tx_pool)
                        if set(self.pool[tx_pool]) == set(input_shards):
                            matching_txs.append(tx_pool)
                        

                    #print('matching_txs: ', matching_txs)
                    #for tx in matching_txs
                    #    print(tx, self.pool[tx])
                    # seng SIGN message to other nodes in this shard
                    sig_p = ecdsa_sign(self.sSK2, json.dumps(matching_txs))
                    '''print("shard ", self.shard_id, " round ", self.epoch, " send SIGN message to other nodes")'''
                    send(-1, ('SIGN', '', (sig_p, matching_txs)))
                    if(ld_cnt == self.shard_num - 1):
                        break
        def handle_messages_sign_recv():
            signers = []
            sign_cnt = 0
            while True:
                try:
                    (sender, msg) = sign_recv.get()
                    sign_cnt += 1
                    #print(type(msg),str(msg)[0:100])
                    '''print('[SIGN] Node %d in shard %d receive SIGN message from %d ' % (self.id, self.shard_id, sender))'''

                    if len(signers) < self.N - self.f:
                        sig_p, txs = msg
                        # for each SIGN message, verify its signature
                        if sender not in signers:
                            try:
                                assert ecdsa_vrfy(self.sPK2s[sender % self.N], json.dumps(txs), sig_p)
                                #print("Sign signature success2")
                            except AssertionError:
                                if self.logger is not None:
                                    self.logger.info("Sign signature failed!")
                                #print("Sign signature fail2")
                                continue
                            signers.append(sender)

                           # receive n-f SIGN messages, verify their signatures, delete these transactions from pool and TXs, and set their outputvalid to 1
                            if len(signers) == self.N - self.f:
                                #TXs = read_pkl_file(self.TXs)
                                cur = self.TXs.cursor()
                                for tx_pool in txs:
                                    '''input_shards, input_valids, output_shard, output_valid = parse_shard_info(tx_pool)
                                    if self.pool[tx_pool] == len(input_shards):
                                        tx_to_append = tx_pool.replace(f'Output Valid: {output_valid}', f'Output Valid: {1}')
                                        TXs.remove(tx_pool)
                                        TXs.append(tx_to_append)
                                        del self.pool[tx_pool]'''
                                    _, _, _, output_valid = parse_shard_info(tx_pool)
                                    tx_to_append = tx_pool.replace(f'Output Valid: {output_valid}', f'Output Valid: {1}')
                                    #TXs.remove(tx_pool)
                                    #TXs.append(tx_to_append)
                                    cur.execute('INSERT INTO txlist (tx) VALUES (?)', (tx_to_append,))
                                    self.TXs.commit()
                                    del self.pool[tx_pool]

                                #print(self.id, self.shard_id)
                                #write_pkl_file(TXs, self.TXs)

                        #if sign_cnt >= self.N - self.f:
                        #    break
                except Exception as e:
                    continue

        def handle_message_clm_recv():
            clm_signers = set()
            clm_signs = dict()
            while True:
                    try:
                        (sender, msg) = clm_recv.get()

                        if len(clm_signers) < self.N - self.f:
                            txs, sig = msg
                            #print('[CL_M] Node %d in shard %d receive CL_M message from %d ' % (
                            #self.id, self.shard_id, sender))
                            if sender not in clm_signers:
                                try:
                                    assert ecdsa_vrfy(self.sPK2s[sender % self.N], json.dumps(txs), sig)
                                    # print("CL_M signature verified!")
                                except AssertionError:
                                    #print("CL_M ecdsa signature failed!")
                                    if self.logger is not None:
                                        self.logger.info("CL_M ecdsa signature failed!")
                                    continue

                                clm_signers.add(sender)
                                clm_signs[sender] = sig

                                if len(clm_signers) == self.N - self.f and self.id == 0:
                                    Sigma = tuple(clm_signs.items())
                                    grouped_invalid_txs = defaultdict(list)
                                    for tx in txs:
                                        _, _, output_shard, _ = parse_shard_info(tx)
                                        grouped_invalid_txs[output_shard].append(tx)
                                    for shard in grouped_invalid_txs:
                                        for i in range(self.N):
                                            send(i + shard * self.N, ('CL', '', (txs, Sigma)))
                    except Exception as e:
                        print(e)
                        continue

        def handle_message_cl_recv():
            while True:
                    try:
                        (sender, msg) = cl_recv.get()
                        txs, Sigma = msg
                        #print('[CL] Node %d in shard %d receive CL message from %d ' % (
                        #self.id, self.shard_id, sender))
                        try:
                            for item in Sigma:
                                (sender, sig_p) = item
                                assert ecdsa_vrfy(self.sPK2s[sender % self.N], json.dumps(txs), sig_p)
                                # print("CL signature verified!")
                        except AssertionError:
                            #print("CL ecdsa signature failed!")
                            if self.logger is not None:
                                self.logger.info("CL ecdsa signature failed!")
                            continue

                        for tx in txs:
                            input_shards, _, output_shard, _ = parse_shard_info(tx)
                            if self.shard_id in input_shards:
                                '''TODO: 本分片作为输入分片 已对该交易进行了BFT共识 需要构造BACK-TX 将该输入退还至原地址''' 
                                pass
                            
                            if self.shard_id == output_shard:
                                if tx in self.pool:
                                    #print("DELETE")
                                    del self.pool[tx]                       
                    except Exception as e:
                        print(e)
                        continue            

        gevent.spawn(handle_message_clm_recv)
        gevent.spawn(handle_message_cl_recv)
        gevent.spawn(handle_messages_break_recv)

        tx_invalid = []
        #print(self.shard_id, 'before ', len(tx_to_send), tx_to_send)
        for tx in tx_to_send:
            input_shards, input_valids, _, _ = parse_shard_info(tx)
            if self.shard_id in input_shards and input_valids[input_shards.index(self.shard_id)] == 0:
                tx_invalid.append(tx)
        if tx_invalid:
            send(-1, ('CL_M', '', (tx_invalid, ecdsa_sign(self.sSK2, json.dumps(tx_invalid)))))
        tx_to_send = [tx for tx in tx_to_send if tx not in tx_invalid]
        #print(self.shard_id, 'after ', len(tx_to_send), tx_to_send)

        def _setup_fastpath(leader):

            def fastpath_send(k, o):
                send(k, ('FAST', '', o))

            def fastpath_output(o):
                nonlocal latest_notarized_block

                latest_notarized_block = o

            fast_thread = gevent.spawn(hsfastpath, epoch_id, self.shard_id, pid, N, f, leader,
                                   tx_to_send, fastpath_output,
                                   self.SLOTS_NUM, self.FAST_BATCH_SIZE, T,
                                   hash_genesis, self.sPK2s, self.sSK2,
                                   fast_recv.get, fastpath_send, logger=self.logger, omitfast=self.omitfast)

            return fast_thread

        # Start the fast path

        fast_thread = _setup_fastpath(leader)
        fast_thread.get()

        voters = set()
        votes = dict()
        decides = Queue(1)

        gevent.spawn(handle_messages_vote_recv)
        gevent.spawn(handle_messages_ld_recv)
        gevent.spawn(handle_messages_sign_recv)


        tx_batch = latest_notarized_block[3]
        # tx_batch = json.dumps(tx_to_send)
        if self.logger != None:
            tx_cnt = str(json.loads(tx_batch)).count("Dummy TX")
            self.txcnt += tx_cnt
            self.logger.info('Node %d Delivers Hotstuff Block with having %d TXs' % (self.id, tx_cnt))
            end = time.time()
            self.logger.info('Hotstuff Block Delay at Node %d: ' % self.id + str(end - self.s_time))
            self.logger.info('Current Block\'s TPS at Node %d: ' % self.id + str(tx_cnt / (end - self.s_time)))


        merkle_tree = group_and_build_merkle_tree(tx_batch)
        rt = merkle_tree[0][1]

        try:
            sig_prev = ecdsa_sign(self.sSK2, rt)
        except AttributeError as e:
            if self.logger is not None:
                self.logger.info(traceback.print_exc())

        send(-1, ('VOTE', '', (tx_batch, rt, sig_prev)))
        txs, rt, Sigma = decides.get()
        merkletree, shard_branch, positions = group_and_build_merkle_tree(txs)
        #print(merkletree,shard_branch)
        rt = merkletree[1]


        # broadcast LD message except itself
        grouped_txs = defaultdict(list)
        txs_list = json.loads(txs)
        for tx in txs_list:
            _, _, output_shard, _ = parse_shard_info(tx)
            grouped_txs[output_shard].append(tx)
        
        if self.id == 0:
            for shard_id in grouped_txs:
                for i in range(N):
                    send(i + shard_id * N, ('LD', '', (grouped_txs[shard_id], Sigma, rt, shard_branch, positions)))

        if self.id == 1:
                send(-4, ('BREAK', '', ()))

        while True:
            if break_count == self.shard_num:
            #if break_count == self.N:
                break
            time.sleep(0)
        
        #time.sleep(10)
        cur = self.TXs.cursor()
        cur.execute('SELECT * FROM txlist')
        TXs = cur.fetchall()
        self.logger.info(f"after round {self.round} , node {self.id} in shard {self.shard_id} exists {len(TXs)} txs")
        print(f"after round {self.round} , node {self.id} in shard {self.shard_id} exists {len(TXs)} txs")
        recv_t.kill()
