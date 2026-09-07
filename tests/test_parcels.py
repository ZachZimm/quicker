import pytest
from quicker.contracts import Extraction
from quicker.db import Candidate
from quicker.documents import ingest
from quicker.profile import washoe_property_from_parcel
from quicker.review import create_candidates
from sqlalchemy import select


@pytest.mark.parametrize('parcel', ['00714311', '007-143-11', ' 007 143 11 '])
def test_parcel_separators_preserve_identity(parcel):
    assert washoe_property_from_parcel(parcel) == 'Bell St.'


@pytest.mark.parametrize('parcel', [None, '714311', '00714312', 'APN00714311'])
def test_unknown_or_incomplete_parcel_is_not_guessed(parcel):
    assert washoe_property_from_parcel(parcel) is None


@pytest.mark.parametrize('payee,paid,expected', [
    ('Washoe County Treasurer', True, 'Bell St.'),
    ('Other County Treasurer', True, None),
    ('Washoe County Treasurer', False, None),
])
def test_paid_tax_mapping_and_payment_year_account(db, photo, payee, paid, expected):
    doc_id = ingest(db, [('stub.png', photo)], 'parcel-test')[0]
    extraction = Extraction.model_validate({
        'document_type': 'tax', 'transactions': [{
            'kind': 'tax', 'payee': payee, 'paid': paid,
            'date': '2026-08-10', 'date_basis': 'payment',
            'amount': '403.11', 'parcel': '00714311',
        }],
    })
    with db.write() as session:
        ignored = create_candidates(session, doc_id, extraction)
        row = session.scalar(select(Candidate))
        if not paid:
            assert row is None and len(ignored) == 1
        else:
            assert row.data['property'] == expected
            assert row.data['account'] == ('2026 Bell St.' if expected else None)
            assert row.status == 'review'
