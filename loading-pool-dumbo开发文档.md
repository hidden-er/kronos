### 网络搭建逻辑

#### 网络客户端逻辑（节点发送到其他节点）

* `run_socket_node.py---net_client.start()`调用`socket_client.py----NetworkClient::run()`，因为后者继承于`Process`；

* `run`只做了一件事情，就是调用`self._connect_and_send_forever()`；

* `self._connect_and_send_forever()`：

​	（1）先基于节点列表创建自己到所有其他节点的套接字（连接`j`节点的套接字创建通过`self._connect(j)`实现，储存在`self.socks[j]`中，通过`self.is_out_sock_connected[j]`标识创建成功与否；

​	（2）启动自己到所有其他节点的`send`协程（`self.__dynamic_send(j)`)，这些协程从队列`self.sock_queues[j]`中获取消息，通过`self.socks[j]`发送到对端节点；这些协程在网络没寄时一直运行着。

​	（3）最后调用`self._handle_send_loop()`

* `self._handle_send_loop()`从`self.client_from_bft()`接收**消息**（消息中分为节点编号和消息本身两个内容），然后根据情况按不同的逻辑通过方法`put_nowait`将该消息加入队列`self.sock_queues`。接下来主要关注`self.client_from_bft()`

* `self.client_from_bft()` 是`run_socket_node.py`里这么来的：

  ​      ` client_bft_mpq = mpQueue()`

  ​     `client_from_bft = lambda: client_bft_mpq.get(timeout=0.1)`

  ​      即，这玩意不断从`client_bft_mpq`队列中尝试获取消息。

* `client_bft_mpq`队列中的消息是怎么来的？     

  ```python
  #run_socket_node.py
  bft_to_client = client_bft_mpq.put_nowait
  bft = DumboBFTNode(......, bft_to_client, ......)
  
  #dumbo_node.py
  class DumboBFTNode (Dumbo):
  	self.bft_to_client = bft_to_client
  	self.send = lambda j, o: self.bft_to_client((j, o))
  	Dumbo.__init__(.....,self.send,.....)
  
  
  #dumbo.py 
  class Dumbo():
      self._send = send
      def _send(j, msg):
          self._send(j, msg)
      _send(-3, ('LD', ("xxxxx")))
  ```
  
   是通过最后的`_send`方法放进去的。至此，逻辑就通了。

再简单的颠过来说一下执行时的逻辑：`_send`把消息放到`client_bft_mpq`队列里，`self._handle_send_loop()`解析消息并放到对应的`sock_queues[j]`队列里，`__dynamic_send(j)`协程真正把他们发送到对端节点。后两个调用都是一直loop隐式的，所以比较难跟踪。



#### 网络服务端逻辑（节点从其他节点接收）

有了上面的分析，这个就没难度了。

* `run_socket_node.py---net_server.start()`调用`socket_server.py----NetworkClient::run()`，因为后者继承于`Process`；

* `run`只做了一件事情，就是调用`self._listen_and_recv_forever()`；
* `self._listen_and_recv_forever()`

​		（1）内嵌`_handler`函数，其参数（套接字和`IP:Port`）由`StreamServer`自动提供；其通过`self._address_to_id`获取节点编号，并生成**消息**，使用`self.server_to_bft((j, o))`放入消息。**所有消息都放入一条队列中**，（即下面提到的`server_bft_mpq`）

​		（2）`StreamServer`监听来自该 IP 地址和端口的所有入站连接并调用`_handler`处理，且前者一直循环运行。

​			接下来主要关注`self.server_to_bft((j, o))`

* `self.server_to_bft()` 是`run_socket_node.py`里这么来的：

  `server_bft_mpq = mpQueue()`

​		`server_to_bft = server_bft_mpq.put_nowait`

 		即，这玩意不断将消息放入`server_bft_mpq`队列中。

* `server_bft_mpq`队列中的消息是怎么被处理的？

```python
bft_from_server = lambda: server_bft_mpq.get(timeout=0.1)
bft = DumboBFTNode(......, bft_from_server, ......)


#dumbo_node.py

class DumboBFTNode (Dumbo):
	self.bft_from_server = bft_from_server
	self.recv = lambda: self.bft_from_server()
	Dumbo.__init__(.....,self.recv,.....)


#dumbo.py 

class Dumbo():
	self._recv = recv 
	self._recv()  
```

​		是通过最后的`_recv`方法拿出来的。至此，逻辑就通了。

再简单的颠过来说一下执行时的逻辑：`_recv`从`server_bft_mpq`队列里获取消息，这些消息是`self._listen_and_recv_forever()`起的永久运行的`StreamServer`使用`_handler`函数放进去的。`StreamServer`是多协程的，所以即使一个自己的`IP:Port`被不同`IP:Port`发送多个连接，也不会造成消息丢失。





### 交易处理逻辑

#### 交易内容

![image-20231206162118620](D:\markdown_photo\代码复现\image-20231206162118620.png)

（1）输入分片个数为1且输入输出分片相同的为**片内交易**，忽略`Output Valid`值，将可用的（`input valid`为1）直接扔进片内共识处理，紧接着就在自己的TXs里删掉，不参与片间通信。

（2）其他情况都为**片间交易**。对于输入分片，若其对应位置的`Input Valid`为1，则需要该分片处理，否则不需要。若每个参与的输入分片均完成了处理，则将`Output Valid`置1，下一轮输出分片会对其进行处理；否则该交易最终会被退回。*这里初始状态各分片都能判断交易会不会被退回，对于退回交易分片的处理看起来是多余的；但真实情况下前述条件不成立，所以各输入分片必须先处理着。*对于输出分片，若其对应位置的`Output Valid`为1，则需要该分片处理，否则不需要。





#### 交易处理

（1）每轮`bft`开始之前，各节点分别从自己的交易记录本（`self.TXs`）中选取批次量个可处理的输入交易和输出交易，并放入缓冲区

​			*`dumbo_node.py---prepare_bootsrap()`*

（2）片内`bft`内容处理完之后，节点立刻根据对`bft`输出进行处理，具体来说就是把处理好的组内交易从自己的交易记录本里删去

​			*`dumbo.py---Dumbo::_run_round()`主流程中，发送`LD`信息之前*

（3）收到其他分片的`LD`消息时，根据消息中的交易信息对交易池（`self.pool`）中的交易状态进行处理。**目前交易池逻辑是计次，计划改为记已发送列表**

​			*`dumbo.py---Dumbo::__run_round::handle_messages_ld_recv()`*

（4）对于`LD`消息的片内共识`sign`消息到达门限时，根据原`LD`消息中的交易信息对交易记录本（`self.TXs`）和交易池（`self.pool`）进行处理，删除交易池中输入分片全部处理完的交易，将交易记录本中对应该交易的`Output Valid`改为1

​			*`dumbo.py---Dumbo::__run_round::handle_messages_sign_recv()`*

​	**逻辑好像还是有点问题，应该把（3）中的操作也移到（4）中来？？**
