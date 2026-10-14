"""Bounded pagination with legacy offsets and explicit page/page_size aliases."""
def pagination(args,default=100,maximum=1000):
    if 'page' in args or 'page_size' in args:
        if 'limit' in args or 'offset' in args:
            raise ValueError('page/page_size 与 limit/offset 不能混用')
        size=int(args.get('page_size',default));page=int(args.get('page',1))
        if page<1: raise ValueError('page 从 1 开始')
        limit,offset=size,(page-1)*size
    else:
        limit=int(args.get('limit',default));offset=int(args.get('offset',0))
    if not 1<=limit<=maximum or not 0<=offset<=10_000_000:
        raise ValueError('分页参数超出范围')
    return limit,offset
