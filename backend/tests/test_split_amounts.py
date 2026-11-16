import pytest
from app.services.split_amounts import allocate, random_weights
from app.routes.distribute import DisperseBody


@pytest.mark.parametrize('total',[0,1,99,100,10**27+17])
def test_weighted_splits_conserve_every_base_unit(total):
    weights=[1,3,7,13]
    pieces=allocate(total,weights)
    assert sum(pieces)==total and all(p>=0 for p in pieces)
    assert max(abs(p*sum(weights)-total*w) for p,w in zip(pieces,weights))<sum(weights)


def test_random_is_fixed_by_review_seed_and_recipient_order():
    seed='frozen-review-seed-123'
    a=random_weights(seed,'sender',['a','b','c'])
    assert a==random_weights(seed,'sender',['a','b','c'])
    assert a!=random_weights(seed+'2','sender',['a','b','c'])
    assert len(set(a))>1 and sum(allocate(10**24+1,a))==10**24+1
    with pytest.raises(ValueError):
        DisperseBody(from_wallet_id='source',to_wallet_ids=['a','b'],split='random',total='1')


@pytest.mark.parametrize('invalid', [True, 1.5, '2', 0, 10001])
def test_weights_require_bounded_integer_json_values(invalid):
    with pytest.raises(ValueError):
        DisperseBody(from_wallet_id='source', to_wallet_ids=['a'], split='weighted', total='1', weights=[invalid])
