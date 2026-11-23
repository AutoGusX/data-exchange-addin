"""Generate the mock fixture STEP files (AP214, millimetres, B-rep boxes).

Usage: python scripts/make_fixture_step.py  -> writes tests/fixtures/building-core-rev{1,2}.step

Hand-written on purpose: no CAD kernel needed, no third-party sample files in the repo.
Each element is one MANIFOLD_SOLID_BREP box named after its source element id.
"""
import math
import os

ORIGIN = (10000.0, 5000.0, 0.0)  # nonzero translation, so placement errors show up


def box(name, corner, size, rot_z_deg=0.0):
    return {"name": name, "corner": corner, "size": size, "rot": rot_z_deg}


def rev1():
    return [
        box("mock-floor-1", (0, 0, 0), (6000, 4000, 200)),
        box("mock-wall-1", (0, 0, 200), (6000, 200, 3000)),
        box("mock-column-1", (1000, 2000, 200), (300, 300, 3000)),
        box("mock-column-2", (4000, 2000, 200), (300, 300, 3000)),
        box("mock-beam-1", (1000, 2050, 3200), (3300, 200, 300)),
        box("mock-brace-1", (500, 3000, 200), (2000, 100, 100), rot_z_deg=30.0),
    ]


def rev2():
    boxes = rev1()
    boxes[1] = box("mock-wall-1", (0, 0, 200), (6000, 200, 3500))        # geometry change
    boxes.append(box("mock-column-3", (2500, 3200, 200), (300, 300, 3000)))  # element added
    return boxes


class Writer:
    def __init__(self):
        self.lines = []
        self.n = 0

    def add(self, text):
        self.n += 1
        self.lines.append("#%d=%s;" % (self.n, text))
        return "#%d" % self.n


def num(v):
    s = ("%.6f" % v).rstrip("0")
    return s + "0" if s.endswith(".") else s


def vec(v):
    return "(%s)" % ",".join(num(c) for c in v)


def add_box(w, b):
    a = math.radians(b["rot"])
    ax = (math.cos(a), math.sin(a), 0.0)
    ay = (-math.sin(a), math.cos(a), 0.0)
    az = (0.0, 0.0, 1.0)
    axes = (ax, ay, az)
    o = tuple(ORIGIN[i] + b["corner"][i] for i in range(3))

    def corner(i, j, k):
        f = (i * b["size"][0], j * b["size"][1], k * b["size"][2])
        return tuple(o[c] + f[0] * ax[c] + f[1] * ay[c] + f[2] * az[c] for c in range(3))

    def neg(v):
        return tuple(-c for c in v)

    pts, verts = {}, {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                p = corner(i, j, k)
                pts[(i, j, k)] = p
                verts[(i, j, k)] = w.add("VERTEX_POINT('',%s)" % w.add("CARTESIAN_POINT('',%s)" % vec(p)))

    edges = {}

    def edge(v1, v2):
        key = frozenset((v1, v2))
        if key not in edges:
            p1, p2 = pts[v1], pts[v2]
            d = tuple(p2[c] - p1[c] for c in range(3))
            length = math.sqrt(sum(c * c for c in d))
            direction = w.add("DIRECTION('',%s)" % vec(tuple(c / length for c in d)))
            vector = w.add("VECTOR('',%s,%s)" % (direction, num(length)))
            line = w.add("LINE('',%s,%s)" % (w.add("CARTESIAN_POINT('',%s)" % vec(p1)), vector))
            edges[key] = (w.add("EDGE_CURVE('',%s,%s,%s,.T.)" % (verts[v1], verts[v2], line)), v1)
        ref, start = edges[key]
        return w.add("ORIENTED_EDGE('',*,*,%s,%s)" % (ref, ".T." if start == v1 else ".F."))

    # Loops are counter-clockwise seen from outside, so every face normal points out of the solid.
    faces = [
        (neg(az), ax, [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)]),
        (az, ax, [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
        (neg(ay), ax, [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
        (ay, ax, [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
        (neg(ax), ay, [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
        (ax, ay, [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
    ]
    face_refs = []
    for normal, ref_dir, loop in faces:
        oriented = [edge(loop[i], loop[(i + 1) % 4]) for i in range(4)]
        bound = w.add("FACE_OUTER_BOUND('',%s,.T.)" % w.add("EDGE_LOOP('',(%s))" % ",".join(oriented)))
        placement = w.add("AXIS2_PLACEMENT_3D('',%s,%s,%s)" % (
            w.add("CARTESIAN_POINT('',%s)" % vec(pts[loop[0]])),
            w.add("DIRECTION('',%s)" % vec(normal)),
            w.add("DIRECTION('',%s)" % vec(ref_dir))))
        plane = w.add("PLANE('',%s)" % placement)
        face_refs.append(w.add("ADVANCED_FACE('',(%s),%s,.T.)" % (bound, plane)))

    shell = w.add("CLOSED_SHELL('',(%s))" % ",".join(face_refs))
    return w.add("MANIFOLD_SOLID_BREP('%s',%s)" % (b["name"], shell))


def write_step(path, product_name, boxes):
    w = Writer()
    app = w.add("APPLICATION_CONTEXT('automotive design')")
    w.add("APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,%s)" % app)
    pctx = w.add("PRODUCT_CONTEXT('',%s,'mechanical')" % app)
    product = w.add("PRODUCT('%s','%s','',(%s))" % (product_name, product_name, pctx))
    formation = w.add("PRODUCT_DEFINITION_FORMATION('','',%s)" % product)
    pdctx = w.add("PRODUCT_DEFINITION_CONTEXT('part definition',%s,'design')" % app)
    pdef = w.add("PRODUCT_DEFINITION('design','',%s,%s)" % (formation, pdctx))
    pshape = w.add("PRODUCT_DEFINITION_SHAPE('','',%s)" % pdef)
    lu = w.add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
    au = w.add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    su = w.add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    unc = w.add("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-6),%s,'distance_accuracy_value','')" % lu)
    ctx = w.add("(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((%s))"
                "GLOBAL_UNIT_ASSIGNED_CONTEXT((%s,%s,%s))REPRESENTATION_CONTEXT('',''))" % (unc, lu, au, su))
    axis = w.add("AXIS2_PLACEMENT_3D('',%s,%s,%s)" % (
        w.add("CARTESIAN_POINT('',(0.,0.,0.))"),
        w.add("DIRECTION('',(0.,0.,1.))"),
        w.add("DIRECTION('',(1.,0.,0.))")))
    solids = [add_box(w, b) for b in boxes]
    rep = w.add("ADVANCED_BREP_SHAPE_REPRESENTATION('',(%s),%s)" % (",".join([axis] + solids), ctx))
    w.add("SHAPE_DEFINITION_REPRESENTATION(%s,%s)" % (pshape, rep))

    header = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('Data Exchange add-in mock fixture'),'2;1');",
        "FILE_NAME('%s','2026-11-16T00:00:00',(''),(''),'make_fixture_step.py','','');" % os.path.basename(path),
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
        "ENDSEC;",
        "DATA;",
    ]
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(header + w.lines + ["ENDSEC;", "END-ISO-10303-21;", ""]))


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures")
    write_step(os.path.join(out, "building-core-rev1.step"), "Building Core (mock) rev 1", rev1())
    write_step(os.path.join(out, "building-core-rev2.step"), "Building Core (mock) rev 2", rev2())
    print("wrote fixtures to", os.path.normpath(out))
