import re
from gevent import monkey; monkey.patch_all(thread=False)
import pickle
import json
import logging
import os
import traceback, time
import gevent
import numpy as np
from collections import defaultdict, namedtuple
from enum import Enum
from gevent import Greenlet
from gevent.queue import Queue
from merkletree import group_and_build_merkle_tree, merkleVerify, merkleTree
from dumbobft.core.speedydumbocommonsubset import speedydumbocommonsubset
from dumbobft.core.provablebroadcast import provablebroadcast
from dumbobft.core.validators import pb_validate
from dumbobft.core.speedmvbacommonsubset import speedmvbacommonsubset
from honeybadgerbft.core.honeybadger_block import honeybadger_block
from honeybadgerbft.exceptions import UnknownTagError
from crypto.ecdsa.ecdsa import ecdsa_sign, ecdsa_vrfy

def read_pkl_file(file_path):
    with open(file_path, 'rb') as f:
        data = pickle.load(f)
    return data

def write_pkl_file(data, file_path):
    with open(file_path, 'wb') as f:
        pickle.dump(data, f)


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

class BroadcastTag(Enum):
    ACS_PRBC = 'ACS_PRBC'
    ACS_VACS = 'ACS_VACS'
    TPKE = 'TPKE'
    VOTE = 'VOTE'
    LD = 'LD'
    SIGN = 'SIGN'
    CL_M = 'CL_M'
    CL = 'CL'
    BREAK = 'BREAK'

BroadcastReceiverQueues = namedtuple(
    'BroadcastReceiverQueues', ('ACS_PRBC', 'ACS_VACS', 'TPKE', 'VOTE', 'LD', 'SIGN', 'CL_M', 'CL', 'BREAK'))


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

        if tag == BroadcastTag.ACS_PRBC.value:
            recv_queue = recv_queue[j]
        try:
            recv_queue.put_nowait((sender, msg))
        except AttributeError as e:
            print("error", sender, (tag, j, msg))
            traceback.print_exc(e)


class Dumbo():
    """Dumbo object used to run the protocol.

    :param str sid: The base name of the common coin that will be used to
        derive a nonce to uniquely identify the coin.
    :param int pid: Node id.
    :param int B: Batch size of transactions.
    :param int N: Number of nodes in the network.
    :param int f: Number of faulty nodes that can be tolerated.
    :param TBLSPublicKey sPK: Public key of the (f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPrivateKey sSK: Signing key of the (f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPublicKey sPK1: Public key of the (N-f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPrivateKey sSK1: Signing key of the (N-f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param list sPK2s: Public key(s) of ECDSA signature for all N parties.
    :param PrivateKey sSK2: Signing key of ECDSA signature.
    :param str ePK: Public key of the threshold encryption
        (:math:`\mathsf{TPKE}`) scheme.
    :param str eSK: Signing key of the threshold encryption
        (:math:`\mathsf{TPKE}`) scheme.
    :param send:
    :param recv:
    :param K: a test parameter to specify break out after K rounds
    """

    def __init__(self, sid, shard_id, pid, B, shard_num, N, f, sPK, sSK, sPK1, sSK1, sPK2s, sSK2, ePK, eSK, send, recv, logg, K=3,
                 mute=False, debug=False, ):
        self.sid = sid
        self.id = pid
        self.shard_id = shard_id
        self.B = B
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
        self.round = 0  # Current block number
        self.transaction_buffer = Queue()
        self._per_round_recv = {}  # Buffer of incoming messages
        self._ld_recv = Queue()
        self.K = K
        #self.pool = {}
        self.pool = defaultdict(list)

        self.s_time = 0
        self.e_time = 0
        self.txcnt = 0

        self.mute = mute
        self.debug = debug
        self.epoch = 0

    def submit_tx(self, tx):
        """Appends the given transaction to the transaction buffer.
        :param tx: Transaction to append to the buffer.
        """
        #print('backlog_tx', self.id, tx)
        #if self.logger != None:
        #    self.logger.info('Backlogged tx at Node %d:' % self.id + str(tx))
        # Insert transactions to the end of TX buffer
        self.transaction_buffer.put_nowait(tx)

    def run_bft(self):
        """Run the Dumbo protocol."""
        self._stop_recv_loop = False

        def _recv_loop():
            """Receive messages."""
            #print("start recv loop...")
            while not self._stop_recv_loop:
                #gevent.sleep(0)
                try:
                    (sender, (r, msg) ) = self._recv()
                    #self.logger.info('recv1' + str((sender, o)))
                    #print('recv1' + str((sender, o)))
                    # Maintain an *unbounded* recv queue for each epoch
                    if r not in self._per_round_recv:
                        self._per_round_recv[r] = Queue()
                    # Buffer this message
                    self._per_round_recv[r].put_nowait((sender, msg))
                except:
                    continue

        #self._recv_thread = gevent.spawn(_recv_loop)
        self._recv_thread = Greenlet(_recv_loop)
        self._recv_thread.start()

        self.s_time = time.time()


        # For each round...
        #gevent.sleep(0)

        r = self.round
        if r not in self._per_round_recv:
            self._per_round_recv[r] = Queue()

        # Select B transactions (TODO: actual random selection)
        tx_to_send = []
        for _ in range(self.B):
            tx_to_send.append(self.transaction_buffer.get_nowait())

        '''TXs = read_pkl_file(self.TXs)
        TXs = [tx for tx in TXs if tx not in tx_to_send]
        write_pkl_file(TXs, self.TXs)'''

        def _make_send(r):
            def _send(j, o):
                self._send(j, (r, o))
            return _send

        send_r = _make_send(r)
        recv_r = self._per_round_recv[r].get
        self._run_round(r, tx_to_send, send_r, recv_r, self.epoch)


        ### VERY IMPORTANT!!! otherwise the program will be block when running  _run_round the second time
        self._stop_recv_loop = True
        self._recv_thread.kill()

        self.epoch += 1

        if self.logger != None:
            self.e_time = time.time()
            self.logger.info("node %d breaks in %f seconds with total delivered Txs %d" % (self.id, self.e_time-self.s_time, self.txcnt))
        else:
            print("node %d breaks" % self.id)

    #
    def _run_round(self, r, tx_to_send, send, recv, epoch):
        """Run one protocol round.
        :param int r: round id
        :param tx_to_send: Transaction(s) to process.
        :param send:
        :param recv:
        """

        # Unique sid for each round
        sid = self.sid + ':' + str(r)
        pid = self.id
        shard_id = self.shard_id
        N = self.N
        f = self.f
        break_count = 0

        pb_recvs = [Queue() for _ in range(N)]
        vacs_recv = Queue()
        tpke_recv = Queue()
        vote_recv = Queue()
        ld_recv = Queue()
        sign_recv = Queue()
        clm_recv = Queue()
        cl_recv = Queue()
        break_recv = Queue()

        my_pb_input = Queue(1)

        pb_value_outputs = [Queue(1) for _ in range(N)]
        pb_proof_output = Queue(1)
        pb_proofs = dict()

        vacs_input = Queue(1)
        vacs_output = Queue(1)

        recv_queues = BroadcastReceiverQueues(
            ACS_PRBC=pb_recvs,
            ACS_VACS=vacs_recv,
            TPKE=tpke_recv,
            VOTE=vote_recv,
            LD=ld_recv,
            SIGN=sign_recv,
            CL_M=clm_recv,
            CL=cl_recv,
            BREAK=break_recv
        )

        bc_recv_loop_thread = Greenlet(broadcast_receiver_loop, recv, recv_queues)
        bc_recv_loop_thread.start()

        #print(pid, r, 'tx_to_send:', tx_to_send)
        #if self.logger != None:
        #    self.logger.info('Commit tx at Node %d:' % self.id + str(tx_to_send))

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
                    # if len(txs) == 0:
                    #     break

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
                                #print('[FINISH] before set value=1 TXs have %d txs' %len(TXs))
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

                                #print('[FINISH] after set value=1 TXs have %d txs' %len(TXs))
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
                                    print("CL_M ecdsa signature failed!")
                                    continue

                                clm_signers.add(sender)
                                clm_signs[sender] = sig

                                if len(clm_signers) == self.N - self.f:
                                    Sigma = tuple(clm_signs.items())
                                    send(-3, ('CL', '', (txs, Sigma)))
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
                            print("CL ecdsa signature failed!")
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
        #print(self.shard_id, 'before ', len(tx_to_send))
        for tx in tx_to_send:
            input_shards, input_valids, _, _ = parse_shard_info(tx)
            if self.shard_id in input_shards and input_valids[input_shards.index(self.shard_id)] == 0:
                tx_invalid.append(tx)
        if tx_invalid:
            send(-1, ('CL_M', '', (tx_invalid, ecdsa_sign(self.sSK2, json.dumps(tx_invalid)))))
        tx_to_send = [tx for tx in tx_to_send if tx not in tx_invalid]
        #print(self.shard_id, 'after ', len(tx_to_send))

        pb_threads = [None] * N

        def _setup_pb(j):
            """Setup the sub protocols RBC, BA and common coin.
            :param int j: Node index for which the setup is being done.
            """

            def pb_send(k, o):
                """Reliable send operation.
                :param k: Node to send.
                :param o: Value to send.
                """
                send(k, ('ACS_PRBC', j, o))

            # Only leader gets input
            pb_input = my_pb_input.get if j == pid else None

            pb_thread = gevent.spawn(provablebroadcast, sid+'PB'+str(r)+str(j), pid, shard_id, 
                                     N, f, self.sPK2s, self.sSK2, j, pb_input,
                                     pb_value_outputs[j].put_nowait,
                                     recv=pb_recvs[j].get, send=pb_send, logger=self.logger)

            def wait_for_pb_proof():
                proof = pb_thread.get()
                try:
                    pb_proofs[sid+'PB'+str(r)+str(j)] = proof
                    pb_proof_output.put_nowait(proof)
                except TypeError as e:
                    print(e)
                    #return False
            # wait for pb proof, only when I am the leader
            if j == pid:
                gevent.spawn(wait_for_pb_proof)

            return pb_thread

        # N instances of PB
        for j in range(N):
            #print("start to set up RBC %d" % j)
            pb_threads[j] = _setup_pb(j)



        # One instance of (validated) ACS
        #print("start to set up VACS")
        def vacs_send(k, o):
            """Threshold encryption broadcast."""
            """Threshold encryption broadcast."""
            send(k, ('ACS_VACS', '', o))

        def vacs_predicate(j, vj):
            prbc_sid = sid + 'PB' + str(r) + str(j % N)
            try:
                proof = vj
                if prbc_sid in pb_proofs.keys():
                    try:
                        _prbc_sid, _digest, _sigmas = proof
                        assert prbc_sid == _prbc_sid
                        _, digest, _ = pb_proofs[prbc_sid]
                        assert digest == _digest
                        return True
                    except AssertionError:
                        print("1 Failed to verify proof for RBC")
                        return False
                assert pb_validate(prbc_sid, N, f, self.sPK2s, proof)
                pb_proofs[prbc_sid] = proof
                return True
            except AssertionError:
                print("2 Failed to verify proof for RBC")
                return False

        vacs_thread = Greenlet(speedmvbacommonsubset, sid + 'VACS' + str(r), pid, shard_id, N, f,
                               self.sPK, self.sSK, self.sPK1, self.sSK1, self.sPK2s, self.sSK2,
                               vacs_input.get, vacs_output.put_nowait,
                               vacs_recv.get, vacs_send, vacs_predicate, logger=self.logger)
        vacs_thread.start()

        # One instance of TPKE
        def tpke_bcast(o):
            """Threshold encryption broadcast."""
            send(-1, ('TPKE', '', o))

        # One instance of ACS pid, N, f, prbc_out, vacs_in, vacs_out
        dumboacs_thread = Greenlet(speedydumbocommonsubset, pid, N, f,
                           [_.get for _ in pb_value_outputs],
                           pb_proof_output.get,
                           vacs_input.put_nowait,
                           vacs_output.get)

        dumboacs_thread.start()

        #print("shard: ", self.shard_id, "node: ", self.id, " round: ", self.epoch, " ready to honeybadger")
        my_pb_input.put_nowait(json.dumps(tx_to_send))
        _output = dumboacs_thread.get()

        output = []
        for value in _output:
            if value != None:
                output.append(value)
        output = tuple(output)
        #_output = honeybadger_block(pid, self.N, self.f, self.ePK, self.eSK,
        #                            propose=json.dumps(tx_to_send),
        #                            acs_put_in=my_prbc_input.put_nowait, acs_get_out=dumboacs_thread.get,
        #                            tpke_bcast=tpke_bcast, tpke_recv=tpke_recv.get)

        voters = set()
        votes = dict()

        #decides: used to store the final decision
        #decide_sent: used to symbolize whether the final decision is sent
        #print("round: ",self.epoch)
        decides = Queue(1)

        #receive message from 'vote_recv' queue.
        #each message includes 'sender' and 'msg'
        gevent.spawn(handle_messages_vote_recv)
        gevent.spawn(handle_messages_ld_recv)
        gevent.spawn(handle_messages_sign_recv)

        block = set()
        for batch in output:
            decoded_batch = json.loads(batch)
            for tx in decoded_batch:
                block.add(tx)

        if self.logger != None:
            tx_cnt = str(list(block)).count("Dummy TX")
            self.txcnt += tx_cnt
            self.logger.info('Node %d Delivers ACS Block in Round %d with having %d TXs' % (self.id, r, tx_cnt))
            end = time.time()
            self.logger.info('ACS Block Delay at Node %d: ' % self.id + str(end - self.s_time))
            self.logger.info('Current Block\'s TPS at Node %d: ' % self.id + str(tx_cnt / (end - self.s_time)))

        tx_batch = json.dumps(list(block))
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
        # (TODO: Sent according to the shards involved)
        # if self.id == 0:
        #         send(-3, ('LD', '', (txs, Sigma, rt, shard_branch, positions)))
                #print("shard ", self.shard_id, " round ", self.epoch, " send LD message to other shards")
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
        self.logger.info(f"after round {self.epoch} , node {self.id} in shard {self.shard_id} exists {len(TXs)} txs")
        print(f"after round {self.epoch} , node {self.id} in shard {self.shard_id} exists {len(TXs)} txs")
        
        dumboacs_thread.kill()
        bc_recv_loop_thread.kill()
        vacs_thread.kill()
        for j in range(N):
            pb_threads[j].kill()

        return list(block)

    # TODO： make help and callhelp threads to handle the rare cases when vacs (vaba) returns None
