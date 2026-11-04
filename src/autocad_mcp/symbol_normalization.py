"""Plan guarded planar symbol resizes from explicitly qualified local ports.

No drawing mutation or family inference. Callers supply a fresh native index,
review unmatched ports and execute through the bounded revision adapter.
"""
import math


def expected_state(entity):
    fields = dict(reversed(entity['dxf']))
    return {str(k): fields[k] for k in (0,330,410,8,10,41,42,43,50) if k in fields}



def scale_with_connections(symbol, entities, target, ports, *, item, tolerance=0.01, anchor=None):
    """Return scale + endpoint patches, and the native endpoints matched per port.

    Ports are block-local XY, with block base already accounted for. Orthogonal
    pipes must remain orthogonal; an offset anchor requiring rerouting is refused.
    Only open LINE / LWPOLYLINE endpoints can move. Interiors and junctions never
    silently move. Scale signs and insertion rotation are preserved.
    """
    if symbol.get('type') != 'INSERT' or type(target) not in (int, float) or not math.isfinite(target) or target <= 0:
        raise ValueError('PLANAR_INSERT_AND_POSITIVE_TARGET_REQUIRED')
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('FINITE_POSITIVE_TOLERANCE_REQUIRED')
    def finite_point(point, size):
        return (isinstance(point, (list, tuple)) and len(point) == size
                and all(type(v) in (int, float) and math.isfinite(v) for v in point))
    if not finite_point(symbol.get('scale'), 3) or not finite_point(symbol.get('position'), 3):
        raise ValueError('FINITE_TRANSFORM_REQUIRED')
    if not isinstance(ports, (list, tuple)) or not ports or not all(finite_point(p, 2) for p in ports):
        raise ValueError('FINITE_LOCAL_XY_PORTS_REQUIRED')
    sx, sy, sz = symbol['scale']
    if not all(math.isfinite(v) and v != 0 for v in (sx, sy, sz)):
        raise ValueError('FINITE_NONZERO_SCALE_REQUIRED')
    if abs(abs(sx)-abs(sy)) > 1e-6:
        raise ValueError('NONUNIFORM_SYMBOL_REQUIRES_REVIEW')
    angle = symbol.get('rotation', 0)
    if type(angle) not in (int, float) or not math.isfinite(angle):
        raise ValueError('FINITE_ROTATION_REQUIRED')
    co, si = math.cos(angle), math.sin(angle)
    x, y, z = symbol['position']
    normal = dict(reversed(symbol['dxf'])).get(210, [0, 0, 1])
    if normal != [0, 0, 1]:
        raise ValueError('PLANAR_INSERT_NORMAL_REQUIRED')
    if abs(z) > 1e-8:
        raise ValueError('PLANAR_INSERT_REQUIRED')
    def offset(port, scale):
        px = port[0]*math.copysign(scale, sx)
        py = port[1]*math.copysign(scale, sy)
        return [co*px-si*py, si*px+co*py]
    base = [x, y, z]
    if anchor is not None:
        if not finite_point(anchor, 2):
            raise ValueError('FINITE_LOCAL_XY_ANCHOR_REQUIRED')
        old_anchor, new_anchor = offset(anchor, abs(sx)), offset(anchor, target)
        base = [x+old_anchor[0]-new_anchor[0], y+old_anchor[1]-new_anchor[1], z]
    def transform(port, scale, origin):
        px, py = offset(port, scale)
        return [origin[0]+px, origin[1]+py, z]
    patches = {}; matches = []
    for port in ports:
        old, new = transform(port, abs(sx), [x,y,z]), transform(port, target, base)
        found = []
        for e in entities:
            if e.get('space') != symbol.get('space') or e.get('type') not in ('LINE', 'LWPOLYLINE'):
                continue
            d = dict(reversed(e['dxf']))
            if e['type'] == 'LWPOLYLINE':
                if d.get(70, 0)&1 or any(abs(v)>1e-8 for k,v in e['dxf'] if k==42):
                    continue
                pts = [v for k,v in e['dxf'] if k==10]
                ends = [(0, pts[0], pts[1]), (len(pts)-1, pts[-1], pts[-2])] if len(pts)>1 else []
            else:
                ends = [(10, d[10], d[11]), (11, d[11], d[10])]
            for key, point, adjacent in ends:
                if math.dist(point[:2],old[:2]) > tolerance:
                    continue
                found.append({'handle':e['handle'], 'endpoint':key, 'before':point, 'after':new})
                if math.dist(point[:2],new[:2]) < 1e-8:
                    continue
                # Preserve the existing orthogonal direction. A new diagonal is a reroute.
                for axis in (0,1):
                    if abs(point[axis]-adjacent[axis])<1e-6 and abs(new[axis]-adjacent[axis])>tolerance:
                        raise ValueError('ORTHOGONAL_REROUTE_REQUIRED:'+e['handle'])
                p=patches.setdefault(e['handle'], {'entity':e, 'points':{}, 'set':{}})
                p['points' if e['type']=='LWPOLYLINE' else 'set'][key]=new[:len(point)]
        matches.append({'port':port,'before':old,'after':new,'matches':found})
    ops=[dict(item=item,kind='scale',handle=symbol['handle'],expect=expected_state(symbol),target=target)]
    if anchor is not None:
        # Updating the block transform keeps attached lettering at its native size
        # and position. Callers review/reposition lettering separately when needed.
        ops[0] = dict(item=item, kind='patch', handle=symbol['handle'],
                      expect=expected_state(symbol), set={10:base,
                      41:math.copysign(target,sx),42:math.copysign(target,sy),
                      43:math.copysign(target,sz)})
    for h,p in patches.items():
        e=p['entity'];exp=expected_state(e)
        if e['type']=='LINE':
            d=dict(reversed(e['dxf']));exp.update({k:d[k] for k in p['set']})
            ops.append(dict(item=item,kind='patch',handle=h,expect=exp,set=p['set']))
        else:
            old=[v for k,v in e['dxf'] if k==10];new=[p['points'].get(i,v) for i,v in enumerate(old)]
            ops.append(dict(item=item,kind='vertices',handle=h,expect=exp,expected_points=old,points=new))
    if len(ops)>7:
        raise ValueError('BATCH_SIZE_EXCEEDED_RECOMPOSE_REQUIRED')
    return {'operations':ops,'ports':matches}
