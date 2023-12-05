import hashlib
import json
import math


def hash(x):
    assert isinstance(x, (str, bytes))
    try:
        x = x.encode()
    except AttributeError:
        pass
    return hashlib.sha256(x).digest()


def ceil(x): return int(math.ceil(x))


def merkleTree(strList):
    """Builds a merkle tree from a list of :math:`N` strings (:math:`N`
    at least 1)

    :return list: Merkle tree, a list of ``2*ceil(N)`` strings. The root
         digest is at ``tree[1]``, ``tree[0]`` is blank.

    """
    N = len(strList)
    assert N >= 1
    bottomrow = 2 ** ceil(math.log(N, 2))
    mt = [b''] * (2 * bottomrow)
    for i in range(N):
        mt[bottomrow + i] = hash(strList[i])
    for i in range(bottomrow - 1, 0, -1):
        mt[i] = hash(mt[i * 2] + mt[i * 2 + 1])
    return mt


def merkleTree2(strList, output_shards):
    """Builds a merkle tree from a list of :math:`N` strings (:math:`N`
    at least 1)

    :return list: Merkle tree, a list of ``2*ceil(N)`` strings. The root
         digest is at ``tree[1]``, ``tree[0]`` is blank.

    """
    N = len(strList)
    assert N >= 1
    root_positions = {}
    bottomrow = 2 ** ceil(math.log(N, 2))
    mt = [b''] * (2 * bottomrow)
    for i in range(N):
        mt[bottomrow + i] = strList[i]
        root_positions[output_shards[i]] = bottomrow + i
    for i in range(bottomrow - 1, 0, -1):
        mt[i] = hash(mt[i * 2] + mt[i * 2 + 1])
    return mt, root_positions


def mergeMerkleTrees(merkle_trees):
    """Merge multiple Merkle trees into a larger one

    :param merkle_trees: List of Merkle trees
    :return: A tuple containing the new Merkle tree and a list of positions of the root nodes of the subtrees
    """
    # Get the root nodes of the subtrees
    #print(merkle_trees.keys())
    #print(merkle_trees.values())
    root_nodes = [tree[1] for tree in merkle_trees.values()]
    output_shards = [int(output_shard) for output_shard in merkle_trees.keys()]

    # Build a new Merkle tree from the root nodes
    new_tree, root_positions = merkleTree2(root_nodes, output_shards)

    return new_tree, root_positions


def getMerkleBranch(index, mt):
    path = []
    #print(index)
    while index > 1:
        sibling_index = index + 1 if index % 2 == 0 else index - 1
        path.append(mt[sibling_index])
        index = index // 2
    #print(path)
    return path


# Group and build Merkle tree based on 'Output Shard' for transactions
def group_and_build_merkle_tree(tx_batch):
    tx_batch_list = json.loads(tx_batch)
    grouped_tx = {}  # Dictionary for grouping transactions

    # Iterate over each transaction
    for tx in tx_batch_list:
        # Extract 'Output Shard' information
        output_shard = tx.split("Output Shard: ")[1][:-19]

        if output_shard not in grouped_tx:
            grouped_tx[output_shard] = []  # Create a list for transactions in an output shard
        grouped_tx[output_shard].append(tx)  # Add the transaction to the corresponding output shard list
    grouped_tx = dict(sorted(grouped_tx.items()))
    
    merkle_trees = {}  # Store Merkle trees for different output shards

    # Build a Merkle tree for each output shard
    shard_branch = {}
    positions = []
    for output_shard, tx_list in grouped_tx.items():
        merkle_tree = merkleTree(tx_list)
        merkle_trees[output_shard] = merkle_tree  # Store the Merkle tree for each output shard
    # Build the merged Merkle tree
    merged_merkle_tree, positions = mergeMerkleTrees(merkle_trees)
    #print(merkle_trees, positions)

    for output_shard in grouped_tx.keys():
        shard_branch[output_shard] = getMerkleBranch(positions[int(output_shard)], merged_merkle_tree)

    return merged_merkle_tree, shard_branch, positions  # Return the merged Merkle tree


def merkleVerify(key, val, roothash, shard_branch, index):
    """Verify a merkle tree branch proof
    """
    # Get the branch for the given key
    branch = shard_branch[key]
    # Compute the hash of the value
    tmp = val
    tindex = index
    for br in branch:
        tmp = hash((tindex & 1) and br + tmp or tmp + br)
        tindex >>= 1
    if tmp != roothash:
        print("Verification failed with", val, roothash, branch, tmp == roothash)
        return False
    return True

'''
tx = ['<Dummy TX: abc, Input Shard: [0, 2], Output Shard: 1 Finished>',
      '<Dummy TX: abc, Input Shard: [1, 3], Output Shard: 2 Finished>',
      '<Dummy TX: abc, Input Shard: [0, 1], Output Shard: 2 Finished>',
      '<Dummy TX: abc, Input Shard: [2], Output Shard: 3 Finished>',
      '<Dummy TX: abc, Input Shard: [1, 2], Output Shard: 0 Finished>']

tx2 = ['<Dummy TX: abc, Input Shard: [1, 3], Output Shard: 2 Finished>',
      '<Dummy TX: abc, Input Shard: [0, 1], Output Shard: 2 Finished>']
tx_batch = json.dumps(tx)
val = merkleTree(tx2)[1]
merkle_tree, shard_branch, positions = group_and_build_merkle_tree(tx_batch)
print(merkle_tree)
print(shard_branch)
roothash = merkle_tree[1]
index = positions[2]
print(merkleVerify("2", val, roothash, shard_branch, index))
'''