import re

def test_btrfs_show_parser():
    output = """
Label: 'storage-pool'  uuid: 550e8400-e29b-41d4-a716-446655440000
	Total devices 2 FS bytes used 1.20TiB
	devid    1 size 2.00TiB used 1.20TiB path /dev/sdb
	devid    2 size 2.00TiB used 1.20TiB path /dev/sdc

Label: none  uuid: f47ac10b-58cc-4372-a567-0e02b2c3d479
	Total devices 1 FS bytes used 512.00KiB
	devid    1 size 10.00GiB used 2.04GiB path /dev/sdd
"""
    fs_blocks = re.split(r'Label:', output)
    pools = []
    for block in fs_blocks:
        if not block.strip(): continue
        
        uuid_match = re.search(r"uuid:\s+([a-f0-9-]+)", block)
        if not uuid_match: continue
        
        uuid_val = uuid_match.group(1)
        label_match = re.match(r"\s*(\'(.*?)\'|\S+)", block)
        label = 'none'
        if label_match:
            label = (label_match.group(2) or label_match.group(1)).strip("'")
            if label == 'none': label = 'Unlabeled'
            
        pool = {
            'id': uuid_val,
            'name': label,
            'uuid': uuid_val,
            'devices': [],
            'total_size': 'Unknown',
            'used_size': 'Unknown',
            'raid_level': 'Single',
            'status': 'healthy'
        }
        
        dev_lines = re.findall(r"path\s+(\S+)", block)
        pool['devices'] = [d.strip() for d in dev_lines]
        
        if 'missing' in block.lower():
            pool['status'] = 'degraded'
        
        pools.append(pool)
    
    assert len(pools) == 2
    assert pools[0]['name'] == 'storage-pool'
    assert pools[0]['devices'] == ['/dev/sdb', '/dev/sdc']
    assert pools[1]['name'] == 'Unlabeled'
    assert pools[1]['devices'] == ['/dev/sdd']
    print("Btrfs show parser test passed!")

def test_btrfs_usage_parsing():
    u_out = """
Overall:
    Device size:		   4.00TiB
    Device allocated:		   2.40TiB
    Device unallocated:		   1.60TiB
    Device missing:		     0.00B
    Device slack:		     0.00B
    Used:			   1.20TiB
    Free (estimated):		   1.80TiB	(min: 1.80TiB)
    Free (statfs):		   1.80TiB
    Data ratio:			      2.00
    Metadata ratio:		      2.00
    Global reserve:		 512.00MiB	(used: 0.00B)
    Multiple profiles:		        no

Data,RAID1: Size:1.20TiB, Used:1.15TiB (95.83%)
   /dev/sdb	   1.20TiB
   /dev/sdc	   1.20TiB

Metadata,RAID1: Size:2.00GiB, Used:1.50GiB (75.00%)
   /dev/sdb	   2.00GiB
   /dev/sdc	   2.00GiB

System,RAID1: Size:32.00MiB, Used:16.00KiB (0.05%)
   /dev/sdb	  32.00MiB
   /dev/sdc	  32.00MiB

Unallocated:
   /dev/sdb	 817.97GiB
   /dev/sdc	 817.97GiB
"""
    raid_level = 'Single'
    if 'RAID1' in u_out: raid_level = 'RAID1'
    elif 'RAID10' in u_out: raid_level = 'RAID10'
    elif 'RAID0' in u_out: raid_level = 'RAID0'
    
    u_match = re.search(r"Used:\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
    f_match = re.search(r"Free \(estimated\):\s+(\d+\.?\d*[TiGkMBP]i?B)", u_out)
    
    assert raid_level == 'RAID1'
    assert u_match.group(1) == '1.20TiB'
    assert f_match.group(1) == '1.80TiB'
    print("Btrfs usage parsing test passed!")

if __name__ == "__main__":
    test_btrfs_show_parser()
    test_btrfs_usage_parsing()
