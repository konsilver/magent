//你拥有查看context的权利

待定义

//这个agent主要负责从memory提取可能与任务相关的用户特征(A)+从query中提取可能的用户特征(B)，写入context让其他所有agent看见

//在任务最后，删除memory中的A部分（有对应删除接口），然后LLM合并A和B（如果有矛盾的地方，以B为最新标准覆盖A），然后合并结果写入memory