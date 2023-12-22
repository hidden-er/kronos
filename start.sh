#!/bin/sh

echo "start.sh <shard_id> <N> <f> <B> <K>"

rm ./TXs
touch TXs
python3 ./dumbobft/core/tx_generator.py --shard_num $1 --tx_num 20000
python3 run_trusted_key_gen.py --N $2 --f $3


# llall python3      

shard_id=0
while [ "$shard_id" -lt $1 ]; do
    node_id=0
    while [ "$node_id" -lt $(( $2 - 0 )) ]; do
        if [ "$shard_id" -eq 0 ] && [ "$node_id" -eq 0 ]; then
            echo "Skipping node $node_id in shard $shard_id..."
        else
            echo "start node $node_id in shard $shard_id..."
            python3 run_socket_node.py --sid 'sidA' --id $node_id --shard_id $shard_id --shard_num $1 --N $2 --f $3 --B $4 --K $5 &
        fi
        rm "./TXs_file/TXs$(($shard_id * 4 + $node_id))"
        cp ./TXs "./TXs_file/TXs$(($shard_id * 4 + $node_id))"
        node_id=$(( node_id + 1 ))
    done
    shard_id=$(( shard_id + 1 ))
done

#echo "start node 3 in shard 0..."
#python3 run_socket_node.py --sid 'sidA' --id 0 --shard_id 0 --shard_num 2 --N 4 --f 1 --B 1000 --K 1 &
#--sid sidA --id 1 --shard_id 1 --N 4 --f 1 --B 1000 --K 10
