import unittest

from axiom_data import ArtifactError
from axiom_data.source_completeness import SourceCompletenessError, page_evidence
from axiom_data.source_coverage import observation, LEGACY_POLICY
import test_pagination_selectors as fixtures


class SourcePageEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PaginationSelectorsTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_complete_plans_sort_pages_and_keep_selectors_separate(self):
        groups = []
        for endpoint in ('index_member_all', 'ci_index_member'):
            for mode in ('Y', 'N'):
                groups.append([self.fixture.page(offset, count, endpoint=endpoint,
                    selector={'is_new':mode}, row_change={'is_new':mode})
                    for offset, count in ((0,2), (2,1))])
        raw_batches = [page for pages in groups for page in reversed(pages)]
        evidence = page_evidence(raw_batches)
        self.assertEqual(set(evidence), {raw.ref.raw_batch_id for raw in raw_batches})
        for pages in groups:
            for raw in pages:
                selected = evidence[raw.ref.raw_batch_id]
                self.assertEqual(selected, pages)
                self.assertTrue(observation(raw, evidence=selected)['payload_admission']['complete'])
                # The exact v1 projection continues to represent an individual
                # page even if the new caller happens to supply page evidence.
                self.assertEqual(observation(raw, policy=LEGACY_POLICY, evidence=selected),
                                 observation(raw, policy=LEGACY_POLICY))
                self.assertFalse(observation(raw, policy=LEGACY_POLICY)['payload_admission']['complete'])

    def test_missing_first_middle_or_terminal_page_never_completes(self):
        pages = [self.fixture.page(offset, count) for offset, count in ((0,2),(2,2),(4,1))]
        with self.assertRaisesRegex(ArtifactError, 'complete source evidence'):
            observation(pages[0])
        for incomplete in (pages[1:], [pages[0],pages[2]], pages[:2]):
            with self.subTest(ids=[raw.ref.raw_batch_id for raw in incomplete]), self.assertRaises(SourceCompletenessError):
                page_evidence(incomplete)

    def test_duplicate_observations_must_not_be_merged_across_commit_boundaries(self):
        first = [self.fixture.page(offset, count) for offset,count in ((0,2),(2,1))]
        second = [self.fixture.page(offset, count) for offset,count in ((0,2),(2,1))]
        self.assertTrue(page_evidence(first))
        self.assertTrue(page_evidence(second))
        with self.assertRaises(SourceCompletenessError):
            page_evidence(first + second)

    def test_grouping_reuses_shared_scope_and_result_guards(self):
        first = self.fixture.page(0,2,selector={'ts_code':'000001.SZ'})
        for change in ({'row_change':{'is_new':'Y'}},
                       {'row_change':{'ts_code':'600036.SH'}},
                       {'manifest_change':{'summary':{'partial':True}}}):
            wrong = self.fixture.page(2,1,selector={'ts_code':'000001.SZ'},**change)
            with self.assertRaises(SourceCompletenessError) as raised:
                page_evidence([first,wrong])
            self.assertEqual(raised.exception.raw_batch_id,wrong.ref.raw_batch_id)
