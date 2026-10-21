from hypothesis import given,strategies as st
from nativepeers.receipts import RANK,reduce_receipt


@given(st.lists(st.sampled_from(list(RANK)),min_size=1,max_size=50))
def test_stronger_evidence_survives_random_arrival_order(events):
    current='CREATED'
    for event in events:
        previous=current;current=reduce_receipt(current,event)
        assert RANK[current]>=RANK[previous]
    assert RANK[current]==max(RANK[e] for e in events)
