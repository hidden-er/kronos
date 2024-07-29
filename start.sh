#!/bin/sh

echo "start.sh <shard_num> <N> <f> <B> <R> <start_num> <num> <tx_num>"

# ./start.sh 3 4 1 2000 1 0 8 20000

rm ./TXs
touch TXs
python3 ./dumbobft/core/tx_generator.py --shard_num $1 --tx_num $9 --Rrate $11
#python3 run_trusted_key_gen.py --N $2 --f $3


# llall python3      

shard_id=0
while [ "$shard_id" -lt $1 ]; do
    node_id=0
    while [ "$node_id" -lt $2 ]; do
        if [ $(( $shard_id * $2 + $node_id )) -ge $6 ] && [ $(( $shard_id * $2 + $node_id )) -lt $(($6 + $7)) ]; then
          echo "start node $node_id in shard $shard_id..."
          python3 run_socket_node.py --sid 'sidA' --id $node_id --shard_id $shard_id --shard_num $1 --N $2 --f $3 --B $4 --R $5 --tx_num $8 --Crate $10 &
          #rm "./TXs_file/TXs$(($shard_id * $2 + $node_id))"
          rm "./log/consensus-node-$(($shard_id * $2 + $node_id)).log"
          #cp ./TXs "./TXs_file/TXs$(($shard_id * $2 + $node_id))"
        else
          echo "do nothing"
        fi
        node_id=$(( node_id + 1 ))
    done
    shard_id=$(( shard_id + 1 ))
done


#echo "start node 3 in shard 0..."
#python3 run_socket_node.py --sid 'sidA' --id 0 --shard_id 0 --shard_num 2 --N 4 --f 1 --B 1000 --K 1 &
#--sid sidA --id 1 --shard_id 1 --N 4 --f 1 --B 1000 --K 10
