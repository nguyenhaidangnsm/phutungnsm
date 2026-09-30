# -*- coding: utf-8 -*-
"""Chạy: python3 test_part_code_utils.py   (không cần database - dùng cursor giả)."""
import unittest
from part_code_utils import (norm_code, n_sql, norm_index_sql, resolve_codes,
                             group_by_norm, assign_by_norm, find_norm_collisions_sql)


class FakeCur:
    def __init__(self, data):
        self.data, self.rows = data, []

    def execute(self, sql, params):
        table = sql.split('FROM ')[1].split(' WHERE')[0]
        want = set(params[0])
        self.rows = [{'part_code': c} for c in sorted(set(self.data.get(table, []))) if norm_code(c) in want]

    def fetchall(self):
        return self.rows


class T(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(norm_code(' 06410-kfl 850 '), '06410KFL850')
        self.assertEqual(norm_code(None), '')
        self.assertEqual(norm_code('06410KFL850'), norm_code('06410-KFL-850'))

    def test_sql_safety(self):
        self.assertIn('regexp_replace(upper(bp.part_code)', n_sql('bp.part_code'))
        for bad in ('part_code; DROP TABLE x', 'a.b.c', "x'"):
            with self.assertRaises(ValueError):
                n_sql(bad)
        with self.assertRaises(ValueError):
            norm_index_sql('t; DROP')
        self.assertIn('CREATE INDEX IF NOT EXISTS', norm_index_sql('inventory_items'))
        self.assertIn('HAVING', find_norm_collisions_sql('inventory_items'))

    def test_resolve(self):
        c = FakeCur({'inventory_items': ['06410KFL850', '06420KFL890SS', 'AB-12', 'AB12'],
                     'part_prices': ['99999XYZ001'], 'order_lock_items': ['77777LOCK1']})
        m = resolve_codes(c, ['06410-kfl-850', '06410KFL850', '06420 kfl 890 ss',
                              '99999-xyz-001', '77777lock1', 'ab 12', 'ZZ-000', ' ', None])
        self.assertEqual(m['06410-kfl-850'], '06410KFL850')
        self.assertNotIn('06410KFL850', m)          # đã đúng -> không đổi
        self.assertEqual(m['06420 kfl 890 ss'], '06420KFL890SS')
        self.assertEqual(m['99999-xyz-001'], '99999XYZ001')   # nguồn dự phòng
        self.assertEqual(m['77777lock1'], '77777LOCK1')
        self.assertNotIn('ab 12', m)                # mơ hồ (AB-12 vs AB12) -> giữ nguyên
        self.assertNotIn('ZZ-000', m)               # không tìm thấy -> giữ nguyên
        self.assertEqual(resolve_codes(c, []), {})

    def test_group_assign(self):
        g = group_by_norm(['A-1', 'a1', 'B2', None, '--'])
        self.assertEqual(g, {'A1': ['A-1', 'a1'], 'B2': ['B2']})
        out = {}
        assign_by_norm(out, g, 'A1', 1)
        assign_by_norm(out, g, 'a1', 2)             # khớp chính xác 'a1' thắng
        self.assertEqual(out, {'A-1': 1, 'a1': 2})


if __name__ == '__main__':
    unittest.main(verbosity=2)
