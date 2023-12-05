import pickle

#import portalocker
from gevent import monkey

monkey.patch_all(thread=False)

import re
import json
import logging
import os
import traceback, time
import gevent
import numpy as np
from collections import namedtuple
from enum import Enum
from gevent import Greenlet
from gevent.queue import Queue
from merkletree import group_and_build_merkle_tree, merkleVerify, merkleTree
from dumbobft.core.dumbocommonsubset import dumbocommonsubset
from dumbobft.core.provablereliablebroadcast import provablereliablebroadcast
from dumbobft.core.validatedcommonsubset import validatedcommonsubset
from dumbobft.core.validators import prbc_validate
from honeybadgerbft.core.honeybadger_block import honeybadger_block
from honeybadgerbft.exceptions import UnknownTagError
from crypto.ecdsa.ecdsa import ecdsa_sign, ecdsa_vrfy
from multiprocessing import Lock

lock = Lock()

def read_pkl_file(file_path):
    while True:
        try:
            with lock:
                with open(file_path, 'rb') as f:
                    data = pickle.load(f)
            #with open(file_path, 'rb') as f:
                #data = pickle.load(f))
            return data
        except:
            continue

def write_pkl_file(data, file_path):
    with lock:
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


def set_consensus_log(id: int):
    logger = logging.getLogger("consensus-node-" + str(id))
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s %(filename)s [line:%(lineno)d] %(funcName)s %(levelname)s %(message)s ')
    if 'log' not in os.listdir(os.getcwd()):
        os.mkdir(os.getcwd() + '/log')
    full_path = os.path.realpath(os.getcwd()) + '/log/' + "consensus-node-" + str(id) + ".log"
    file_handler = logging.FileHandler(full_path)
    file_handler.setFormatter(formatter)  # 可以通过setFormatter指定输出格式
    logger.addHandler(file_handler)
    return logger

class BroadcastTag(Enum):
    ACS_PRBC = 'ACS_PRBC'
    ACS_VACS = 'ACS_VACS'
    TPKE = 'TPKE'
    VOTE = 'VOTE'
    LD = 'LD'
    SIGN = 'SIGN'

BroadcastReceiverQueues = namedtuple(
    'BroadcastReceiverQueues', ('ACS_PRBC', 'ACS_VACS', 'TPKE', 'VOTE', 'LD', 'SIGN'))


def broadcast_receiver_loop(recv_func, recv_queues):
    while True:
        # gevent.sleep(0)
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

    def __init__(self, sid, shard_id, pid, B, shard_num, N, f, sPK, sSK, sPK1, sSK1, sPK2s, sSK2, ePK, eSK, send, recv, K=3,
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
        self.logger = set_consensus_log(pid + shard_id * N)
        self.round = 0  # Current block number
        self.transaction_buffer = Queue()
        self._per_round_recv = {}  # Buffer of incoming messages
        self._ld_recv = Queue()
        self.K = K
        self.pool = []

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
        self.transaction_buffer.put_nowait(tx)
    def run_bft(self):
        """Run the Dumbo protocol."""
        if self.mute:
            muted_nodes = [each * 3 + 1 for each in range(int((self.N - 1) / 3))]
            if self.id in muted_nodes:
                # T = 0.00001
                while True:
                    time.sleep(10)

        self._stop_recv_loop = False

        def _recv_loop():
            """Receive messages."""
            # print("start recv loop...")
            while not self._stop_recv_loop:
                try:
                    (sender, (r, msg)) = self._recv()
                    if r not in self._per_round_recv:
                        self._per_round_recv[r] = Queue()
                    # Buffer this message
                    self._per_round_recv[r].put_nowait((sender, msg))
                except:
                    continue

        self._recv_thread = Greenlet(_recv_loop)
        self._recv_thread.start()

        self.s_time = time.time()
        print("shard: ", self.shard_id, "node: ", self.id, " round: ", self.epoch, " starts Dumbo BFT consensus")

        start = time.time()

        r = self.round
        if r not in self._per_round_recv:
            self._per_round_recv[r] = Queue()

        # Select B transactions (TODO: actual random selection)
        tx_to_send = []
        for _ in range(self.B):
            tx_to_send.append(self.transaction_buffer.get_nowait())

        def _make_send(r):
            def _send(j, o):
                self._send(j, (r, o))

            return _send

        send_r = _make_send(r)
        recv_r = self._per_round_recv[r].get

        #tx_batch refer to the txs in TX which operated in this epoch(BFT) in this node
        #proof is its proof
        self._run_round(r, tx_to_send, send_r, recv_r, self.epoch)

        ### VERY IMPORTANT!!! otherwise the program will be block when running  _run_round the second time
        self._stop_recv_loop = True
        self._recv_thread.kill()

        self.epoch += 1

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

        prbc_recvs = [Queue() for _ in range(N)]
        vacs_recv = Queue()
        tpke_recv = Queue()
        vote_recv = Queue()
        ld_recv = Queue()
        sign_recv = Queue()

        my_prbc_input = Queue(1)

        prbc_outputs = [Queue(1) for _ in range(N)]
        prbc_proofs = dict()

        vacs_input = Queue(1)
        vacs_output = Queue(1)

        recv_queues = BroadcastReceiverQueues(
            ACS_PRBC=prbc_recvs,
            ACS_VACS=vacs_recv,
            TPKE=tpke_recv,
            VOTE=vote_recv,
            LD=ld_recv,
            SIGN=sign_recv,
        )
        bc_recv_loop_thread = Greenlet(broadcast_receiver_loop, recv, recv_queues)
        bc_recv_loop_thread.start()

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
                    tx_batch, proof, rt, shard_branch, positions = msg
                    print('[LD] Node %d in shard %d receive LD message from %d' % (self.id, self.shard_id,sender))
                    #print(tx_batch)        
                    # extract the transactions output shard of which is this shard
                    txs = [tx for tx in json.loads(tx_batch) if parse_shard_info(tx)[2] == self.shard_id]
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

                    TXs = read_pkl_file(self.TXs)
                    txs_update = []
                    for tx in txs:
                        if parse_shard_info(tx)[3] != 1:
                            txs_update.append(tx)
                        else:
                            TXs.remove(tx)

                    write_pkl_file(TXs, self.TXs)
                    self.pool.extend(txs_update)

                    from collections import Counter

                    # find transactions that can be finished in this shard
                    def find_tx(self):
                        tx_counter = Counter(self.pool)
                        matching_txs = [tx for tx, count in tx_counter.items() if
                                        count == len(parse_shard_info(tx)[0])]
                        return matching_txs

                    # matching_txs = json.dumps(find_tx(self))
                    matching_txs = find_tx(self)

                    # seng SIGN message to other nodes in this shard
                    sig_p = ecdsa_sign(self.sSK2, json.dumps(matching_txs))
                    print("shard ", self.shard_id, " round ", self.epoch, " send SIGN message to other nodes")
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
                    print('[SIGN] Node %d in shard %d receive SIGN message from %d ' % (self.id, self.shard_id, sender))

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
                        # txs = json.loads(txs)
                        # delete transactions in txs from pool
                        self.pool = [tx for tx in self.pool if tx not in txs]
                        # delete transactions in txs from TXs
                        TXs = read_pkl_file(self.TXs)
                        TXs_update = [tx for tx in TXs if tx not in txs]
                        # print("execute here2")
                        # set outputvalid to 1 for transactions in txs

                        for tx in txs:
                            # print("[tx]", tx)
                            _, _, _, output_valid = parse_shard_info(tx)
                            tx_raw = tx
                            tx = tx.replace(f'Output Valid: {output_valid}', f'Output Valid: {1}')
                            txs[txs.index(tx_raw)] = tx
                        # add these transactions to TXs
                        TXs_update.extend(txs)
                        # update TXs
                        write_pkl_file(TXs_update, self.TXs)
                        # break
                        #if sign_cnt >= self.N - self.f:
                        #    break
                except Exception as e:
                    continue

        def _setup_prbc(j,epoch):
            """Setup the sub protocols RBC, BA and common coin.
            :param int j: Node index for which the setup is being done.
            """

            def prbc_send(k, o):
                """Reliable send operation.
                :param k: Node to send.
                :param o: Value to send.
                """
                send(k, ('ACS_PRBC', j, o))

            # Only leader gets input
            prbc_input = my_prbc_input.get if j == pid else None

            if self.debug:
                prbc_thread = gevent.spawn(provablereliablebroadcast, sid + 'PRBC' + str(r) + str(j), pid, shard_id, N, f,
                                           self.sPK2s, self.sSK2, j,
                                           prbc_input, prbc_recvs[j].get, prbc_send, self.logger)
            else:
                prbc_thread = gevent.spawn(provablereliablebroadcast, sid + 'PRBC' + str(r) + str(j), pid, shard_id, N, f,
                                           self.sPK2s, self.sSK2, j,
                                           prbc_input, prbc_recvs[j].get, prbc_send, str(epoch)+'e'*10)

            def wait_for_prbc_output():
                value, proof = prbc_thread.get()
                prbc_proofs[sid + 'PRBC' + str(r) + str(j)] = proof
                prbc_outputs[j].put_nowait((value, proof))

            gevent.spawn(wait_for_prbc_output)
        def _setup_vacs():

            def vacs_send(k, o):
                """Threshold encryption broadcast."""
                """Threshold encryption broadcast."""
                send(k, ('ACS_VACS', '', o))

            def vacs_predicate(j, vj):
                prbc_sid = sid + 'PRBC' + str(r) + str(j)
                try:
                    proof = vj
                    if prbc_sid in prbc_proofs.keys():
                        try:
                            _prbc_sid, _roothash, _ = proof
                            assert prbc_sid == _prbc_sid
                            _, roothash, _ = prbc_proofs[prbc_sid]
                            assert roothash == _roothash
                            return True
                        except AssertionError:
                            print("1 Failed to verify proof for PB")
                            return False
                    else:
                        assert prbc_validate(prbc_sid, N, f, self.sPK2s, proof)
                        prbc_proofs[prbc_sid] = proof
                        return True
                except AssertionError:
                    print("2 Failed to verify proof for PB")
                    return False

            if self.debug:
                vacs_thread = Greenlet(validatedcommonsubset, sid + 'VACS' + str(r), pid, shard_id, N, f,
                                       self.sPK, self.sSK, self.sPK1, self.sSK1, self.sPK2s, self.sSK2,
                                       vacs_input.get, vacs_output.put_nowait,
                                       vacs_recv.get, vacs_send, vacs_predicate, self.logger)
            else:
                vacs_thread = Greenlet(validatedcommonsubset, sid + 'VACS' + str(r), pid, shard_id, N, f,
                                       self.sPK, self.sSK, self.sPK1, self.sSK1, self.sPK2s, self.sSK2,
                                       vacs_input.get, vacs_output.put_nowait,
                                       vacs_recv.get, vacs_send, vacs_predicate)
            vacs_thread.start()
        #print("shard: ", self.shard_id, "node: ", self.id, " round: ", self.epoch, " ready to rbc")
        # N instances of PRBC
        for j in range(N):
            _setup_prbc(j,epoch)
        _setup_vacs()
        # One instance of TPKE
        def tpke_bcast(o):
            """Threshold encryption broadcast."""
            send(-1, ('TPKE', '', o))
        # One instance of ACS pid, N, f, prbc_out, vacs_in, vacs_out
        dumboacs_thread = Greenlet(dumbocommonsubset, pid, N, f, [prbc_output.get for prbc_output in prbc_outputs],
                                   vacs_input.put_nowait,
                                   vacs_output.get)
        dumboacs_thread.start()
        #print("shard: ", self.shard_id, "node: ", self.id, " round: ", self.epoch, " ready to honeybadger")
        _output = honeybadger_block(pid, self.N, self.f, self.ePK, self.eSK,
                                    propose=json.dumps(tx_to_send),
                                    acs_put_in=my_prbc_input.put_nowait, acs_get_out=dumboacs_thread.get,
                                    tpke_bcast=tpke_bcast, tpke_recv=tpke_recv.get)
        voters = set()
        votes = dict()

        #decides: used to store the final decision
        #decide_sent: used to symbolize whether the final decision is sent
        #print("round: ",self.epoch)
        decides = Queue(1)

        #receive message from 'vote_recv' queue.
        #each message includes 'sender' and 'msg'
        vote_recv_thread = gevent.spawn(handle_messages_vote_recv)
        ld_recv_thread = gevent.spawn(handle_messages_ld_recv)
        sign_recv_thread = gevent.spawn(handle_messages_sign_recv)


        block = set()  # TXs
        for batch in _output:
            decoded_batch = json.loads(batch.decode())
            for tx in decoded_batch:
                block.add(tx)

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

        #print(self.shard_id,self.id,rt,shard_branch)
        # broadcast LD message except itself
        if self.id == 0:
                send(-3, ('LD', '', (txs, Sigma, rt, shard_branch, positions)))
                print("shard ", self.shard_id, " round ", self.epoch, " send LD message to other shards")
        #print("shard %d node %d gets return values" %(self.shard_id, self.id))

        ### VERY IMPORTANT!!! otherwise the program will be block when running  _run_round the second time

        #vote_recv_thread.join()
        #ld_recv_thread.join()
        #sign_recv_thread.join()
        #vote_recv_thread.kill()
        #ld_recv_thread.kill()
        #sign_recv_thread.kill()
        time.sleep(10)

        bc_recv_loop_thread.kill()



    # TODO： make help and callhelp threads to handle the rare cases when vacs (vaba) returns None


