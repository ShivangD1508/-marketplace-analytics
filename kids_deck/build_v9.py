"""Build Kids_Customer_Analysis_v9: 6-slide consolidated core + v8 content as appendix."""
import copy, sys
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.oxml.ns import qn
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

SRC, OUT = sys.argv[1], sys.argv[2]  # usage: python build_v9.py v8.pptx v9.pptx

INK = RGBColor(0x40, 0x41, 0x40)
NAVY = RGBColor(0x2B, 0x3A, 0x4A)
PURPLE = RGBColor(0x5B, 0x4F, 0xCF)
LILAC = RGBColor(0xED, 0xEB, 0xFA)
GRAY = RGBColor(0x8A, 0x8D, 0x8F)
LIGHT = RGBColor(0xED, 0xEE, 0xEF)
PALE = RGBColor(0xF4, 0xF5, 0xF5)
MID = RGBColor(0xC4, 0xC7, 0xC9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
HEAD, BODY = "Gelica Bk", "CircularXX"

prs = Presentation(SRC)
orig = list(prs.slides)
footer_src = orig[13]  # v8 slide 14 has the standard footer
layout = footer_src.slide_layout


# ---------- helpers ----------
def text(slide, x, y, w, h, runs, size=12, color=INK, font=BODY, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, name=None, spacing_after=0):
    """runs: str | list of paragraphs; paragraph = str | list of (text, {opts})."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing_after:
            p.space_after = Pt(spacing_after)
        segs = para if isinstance(para, list) else [(para, {})]
        for seg, o in segs:
            r = p.add_run()
            r.text = seg
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", False)
            f.color.rgb = o.get("color", color)
    return tb


def box(slide, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE, name=None, line=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = 0.08
    if name:
        s.name = name
    return s


def title(slide, parts, subtitle):
    segs = []
    for i, p in enumerate(parts):
        segs.append((p, {"color": PURPLE if i % 2 else INK}))
    text(slide, 0.55, 0.28, 12.2, 0.7, [segs], size=27, font=HEAD, bold=True,
         anchor=MSO_ANCHOR.MIDDLE, name="Title")
    text(slide, 0.58, 0.98, 12.2, 0.35, subtitle, size=13, color=GRAY, name="Subtitle")


def label(slide, x, y, w, s):
    text(slide, x, y, w, 0.3, s.upper(), size=11, bold=True, color=INK, name="Label")


def sowhat(slide, y, head, body, h=0.75):
    box(slide, 0.55, y, 12.23, h, LILAC, MSO_SHAPE.ROUNDED_RECTANGLE, name="So what")
    text(slide, 0.8, y, 1.7, h, "WHAT TO DO", size=11, bold=True, color=PURPLE,
         anchor=MSO_ANCHOR.MIDDLE)
    text(slide, 2.45, y, 10.1, h, [[(head + "  ", {"bold": True, "color": NAVY}),
                                     (body, {})]], size=13, anchor=MSO_ANCHOR.MIDDLE)


def note(slide, s, y=6.2, h=0.45):
    text(slide, 0.55, y, 12.2, h, s, size=8, color=GRAY, name="Source note")


def add_footer(slide, page):
    for sh in footer_src.shapes:
        if sh.top > Inches(7.0):
            el = copy.deepcopy(sh._element)
            if sh.shape_type == 13:  # picture: re-point the image relationship
                img_part = footer_src.part.related_part(
                    sh._element.blipFill.blip.get(qn("r:embed")))
                rid = slide.part.relate_to(img_part, RT.IMAGE)
                el.find(".//" + qn("a:blip")).set(qn("r:embed"), rid)
            slide.shapes._spTree.append(el)
    set_page(slide, page)


def set_page(slide, page):
    for sh in slide.shapes:
        if sh.has_text_frame and sh.left > Inches(11.3) and sh.top > Inches(7.0):
            t = sh.text_frame.text.strip()
            if t.isdigit() or t.startswith("A"):
                sh.text_frame.paragraphs[0].runs[0].text = str(page)


def style_chart(chart, size=11):
    chart.font.name = BODY
    chart.font.size = Pt(size)
    chart.font.color.rgb = INK
    chart.has_title = False


def quiet_axes(chart, val_visible=False, gridlines=False):
    va = chart.value_axis
    va.visible = val_visible
    va.has_major_gridlines = gridlines
    if gridlines:
        va.major_gridlines.format.line.color.rgb = LIGHT
    va.format.line.fill.background()
    ca = chart.category_axis
    ca.format.line.color.rgb = MID
    ca.has_major_gridlines = False
    ca.tick_labels.font.size = Pt(11)
    ca.tick_labels.font.color.rgb = INK
    from pptx.enum.chart import XL_TICK_MARK
    ca.major_tick_mark = XL_TICK_MARK.NONE


def color_points(series, colors):
    for i, c in enumerate(colors):
        pt = series.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = c


def point_label(series, i, s, color=INK, size=11, bold=True, pos=None):
    dl = series.points[i].data_label
    dl.has_text_frame = True
    dl.text_frame.text = s
    r = dl.text_frame.paragraphs[0].runs[0]
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r.font.name = BODY
    if pos is not None:
        dl.position = pos


new_slides = []


def new_slide():
    s = prs.slides.add_slide(layout)
    for ph in list(s.placeholders):
        ph._element.getparent().remove(ph._element)
    new_slides.append(s)
    return s


# =====================================================================
# Slide 2 - What you asked, what we found, what to do
# =====================================================================
s = new_slide()
title(s, ["Your Questions, Answered on One Page"],
      "The follow-up asks from our last meeting, the answer in a sentence, and the starting point it leads to.")
cols = [(0.55, 2.85, "YOU ASKED"), (3.55, 5.45, "WHAT WE FOUND"), (9.15, 3.0, "WHAT TO DO"),
        (12.2, 0.58, "SLIDE")]
for x, w, h in cols:
    text(s, x, 1.5, w, 0.3, h, size=10, bold=True, color=GRAY,
         align=PP_ALIGN.RIGHT if h == "SLIDE" else PP_ALIGN.LEFT)
rows = [
    ("Is the share decline a space problem?", "Space and sales",
     [("Mostly no. ", {"bold": True}), ("Kids share fell every year on flat space; the FY2026 space cut came after, and sales per foot rose 15%.", {})],
     "Hold Kids space in FY2027", "3"),
    ("Is Kids an acquisition driver? How does it compare with peers?", "Acquisition, peer benchmark",
     [("It reaches many, but drives few. ", {"bold": True}), ("Kids is in 1 in 6 new customers' first baskets, 2× any peer, but leads only about 1 in 14.", {})],
     "Use Kids to keep customers, not to win them", "4"),
    ("Do new Kids customers come back? Does the sub-department matter?", "Kids retention",
     [("Fewest come back. ", {"bold": True}), ("16.8% return in 90 days, lowest of any peer. Where they start in Kids makes no difference.", {})],
     "Welcome series in the first 90 days", "5"),
    ("What is happening to the Kids basket (items, AUR)?", "Basket economics",
     [("Fewer items at higher prices. ", {"bold": True}), ("Kids-only trips are small ($8); baskets with other departments carry 93% of the dollars.", {})],
     "Place Kids next to what it is bought with", "6"),
    ("Who are the best Kids customers?", "Customer demographics",
     [("The top tiers already buy Kids. ", {"bold": True}), ("65% of Core and 51% of Aspiring do; 3.7M of them still do not.", {})],
     "Target Core and Aspiring non-buyers", "7"),
    ("Do birthday parties or coupons change the picture?", "Birthday party, coupon usage",
     [("Not materially. ", {"bold": True}), ("Kids is in 2% of party-day baskets; Kids is couponed less, and less deeply, than the rest of the store.", {})],
     "Party follow-up offer; no coupon change", "App."),
]
y0, rh = 1.85, 0.72
for i, (q, tag, ans, act, pg) in enumerate(rows):
    y = y0 + i * rh
    if i % 2 == 0:
        box(s, 0.45, y, 12.43, rh, PALE, name="Row band")
    text(s, 0.55, y + 0.08, 2.85, rh - 0.1,
         [[(tag.upper(), {"size": 9, "bold": True, "color": PURPLE})], q], size=11, color=INK)
    text(s, 3.55, y, 5.45, rh, [ans], size=12, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 9.15, y, 3.0, rh, act, size=12, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 12.2, y, 0.58, rh, pg, size=11, color=GRAY, align=PP_ALIGN.RIGHT,
         anchor=MSO_ANCHOR.MIDDLE)
note(s, "Periods differ by question and are stated on each slide. Full supporting detail, methodology and the original v8 slides are in the appendix.",
     y=6.3, h=0.3)


# =====================================================================
# Slide 3 - Share and space
# =====================================================================
s = new_slide()
title(s, ["", "Kids", " Is Losing Share, and Space Does Not Explain It"],
      "Kids share of chain sales and Kids fixture space per store, fiscal weeks 1–30 (Feb–Aug) of each year so FY2026 compares like-for-like.")
label(s, 0.55, 1.5, 5.6, "Kids share of chain net sales")
label(s, 6.75, 1.5, 5.6, "Kids lineal feet per store")
yrs = ["FY2023", "FY2024", "FY2025", "FY2026"]
cd = CategoryChartData(); cd.categories = yrs; cd.add_series("Share", (7.75, 7.48, 7.55, 7.12))
ch = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.55), Inches(1.85), Inches(5.6),
                        Inches(2.9), cd).chart
style_chart(ch); quiet_axes(ch); ch.has_legend = False
ch.value_axis.minimum_scale = 0; ch.value_axis.maximum_scale = 9.5
pl = ch.plots[0]; pl.gap_width = 70
color_points(pl.series[0], [PURPLE] * 4)
for i, v in enumerate((7.75, 7.48, 7.55, 7.12)):
    point_label(pl.series[0], i, f"{v:.1f}%", pos=XL_LABEL_POSITION.OUTSIDE_END)
cd = CategoryChartData(); cd.categories = yrs; cd.add_series("Lineal ft", (302, 295, 296, 278))
ch = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(6.75), Inches(1.85), Inches(5.6),
                        Inches(2.9), cd).chart
style_chart(ch); quiet_axes(ch); ch.has_legend = False
ch.value_axis.minimum_scale = 0; ch.value_axis.maximum_scale = 370
pl = ch.plots[0]; pl.gap_width = 70
color_points(pl.series[0], [NAVY, NAVY, NAVY, GRAY])
for i, v in enumerate((302, 295, 296, 278)):
    point_label(pl.series[0], i, f"{v} ft", pos=XL_LABEL_POSITION.OUTSIDE_END)
# reading guides under charts
text(s, 0.55, 4.8, 5.6, 0.5,
     [[("−0.6 pts in three years. ", {"bold": True}),
       ("The decline was already under way in FY2024–FY2025, before any space change.", {})]], size=12)
text(s, 6.75, 4.8, 5.6, 0.5,
     [[("Flat for three years, then −8% in FY2026. ", {"bold": True}),
       ("Kids sales still grew 5.5%, so sales per foot rose about 15%.", {})]], size=12)
sowhat(s, 5.5, "Hold Kids space in FY2027.",
       "The share loss started before the cut, and each foot of Kids space now sells more than it did. Party (+1.0 pt) took most of the share Kids lost.")
note(s, "Share = department net sales ÷ chain net sales, FW1–30 of each fiscal year, net of returns, non-merchandise excluded. Lineal ft = weighted fixture feet for depts 51/55/58 per store, Feb–Aug average. Sources: cdp_unification_mk.enrich_transactions_behaviour; merchandising Kids Footage report. Detail: appendix A4–A6.",
     y=6.4, h=0.4)

# =====================================================================
# Slide 4 - Acquisition waterfall (one denominator)
# =====================================================================
s = new_slide()
title(s, ["", "Kids", " Is in 1 in 6 First Baskets but Leads Only 1 in 14"],
      "Every customer who made their first-ever Michaels purchase, Sep 2025 – Aug 2026, and the role Kids played in that first basket.")
label(s, 0.55, 1.5, 7.6, "Start from all 17.0M new Michaels customers")
cats = ["All new Michaels customers", "No Kids in first basket", "Kids in first basket",
        "Kids a small part", "Kids at least half the basket"]
base = (0, 2.71, 0, 1.26, 0)
vals = (17.01, 14.30, 2.71, 1.45, 1.26)
cd = CategoryChartData(); cd.categories = cats
cd.add_series("base", base); cd.add_series("value", vals)
gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_STACKED, Inches(0.55), Inches(1.85), Inches(7.6),
                        Inches(3.45), cd)
ch = gf.chart; style_chart(ch); quiet_axes(ch); ch.has_legend = False
ch.value_axis.minimum_scale = 0; ch.value_axis.maximum_scale = 19.5
ch.category_axis.tick_labels.font.size = Pt(10)
pl = ch.plots[0]; pl.gap_width = 45; pl.overlap = 100
bs = pl.series[0]; bs.format.fill.background(); bs.format.line.fill.background()
vs = pl.series[1]
color_points(vs, [NAVY, MID, NAVY, MID, PURPLE])
labs = ["17.0M", "−14.3M", "2.7M  ·  16%", "−1.45M", "1.26M  ·  7%"]
for i in (0, 1):
    point_label(vs, i, labs[i], color=WHITE if i == 0 else INK, size=12,
                pos=XL_LABEL_POSITION.INSIDE_END)
# manual labels above the small bars (plot is ~7.6in wide, 5 cats)
for i, l in ((2, "2.71M  ·  16%"), (3, "−1.45M"), (4, "1.26M  ·  7%")):
    top = base[i] + vals[i]
    cx = 0.55 + 0.35 + (i + 0.5) * (7.6 - 0.45) / 5
    yy = 1.85 + 0.15 + (3.45 - 0.75) * (1 - top / 19.5) - 0.24
    text(s, cx - 0.75, yy, 1.5, 0.3, l, size=12, bold=True,
         color=PURPLE if i == 4 else INK, align=PP_ALIGN.CENTER)
text(s, 0.55, 5.35, 7.6, 0.45,
     [[("Read left to right: ", {"bold": True}),
       ("one denominator throughout, all new Michaels customers. Kids sits in 16% of first baskets and is the main reason for about 7%.", {})]],
     size=12)

# right panel: the same 2.71M, two denominators
label(s, 8.6, 1.5, 4.2, "Why you also saw “56%”")
text(s, 8.6, 1.85, 4.2, 0.55, "Same 2.71M people, divided by a different total:", size=12)


def ratio_bar(y, cap, total_lbl, share, share_lbl, rest_lbl):
    text(s, 8.6, y, 4.2, 0.3, [[(cap, {"bold": True})]], size=11)
    w = 4.2
    box(s, 8.6, y + 0.32, w, 0.42, LIGHT, name="Bar total")
    box(s, 8.6, y + 0.32, w * share, 0.42, PURPLE, name="Bar share")
    text(s, 8.68, y + 0.32, w * share, 0.42, share_lbl, size=11, bold=True, color=WHITE,
         anchor=MSO_ANCHOR.MIDDLE)
    text(s, 8.6 + w * share + 0.08, y + 0.32, w * (1 - share) - 0.12, 0.42, rest_lbl, size=10,
         color=INK, anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT)
    text(s, 8.6, y + 0.78, 4.2, 0.3, total_lbl, size=10, color=GRAY)


ratio_bar(2.4, "÷ all new Michaels customers", "Total 17.0M  ·  the view on the left", 0.16,
          "16%", "14.3M bought no Kids")
ratio_bar(3.6, "÷ all first-time Kids buyers", "Total 4.82M  ·  includes 2.11M existing shoppers",
          0.563, "56%", "2.11M already shopped")
text(s, 8.6, 4.75, 4.2, 1.0,
     [[("We now lead with 16% and 7%. ", {"bold": True, "color": NAVY}),
       ("56% describes who Kids' first-timers are, not how many new customers Kids brings in, so it moves to the appendix.", {})]],
     size=12)
sowhat(s, 5.9, "Treat Kids as a retention play, not an acquisition engine.",
       "It already reaches 2× more new customers than any peer (Jewelry Making 7.6%); the gap is what happens next.", h=0.6)
note(s, "New customer = first-ever recorded Michaels purchase (records from Feb 2019) in Sep 2025 – Aug 2026. ‘At least half’ = Kids ≥50% of first-basket spend (46.4% of the 2.71M). Peer figure FW1–30 FY2026. Source: cdp_unification_mk.enrich_transactions_behaviour. Detail: appendix A7, A8, A11, A13.",
     y=6.58, h=0.35)

# =====================================================================
# Slide 5 - Retention
# =====================================================================
s = new_slide()
title(s, ["New ", "Kids", " Customers Return Least, but Returners Buy ", "Kids", " Again"],
      "What happens after a customer new to Michaels makes their first purchase in each department.")
label(s, 0.55, 1.5, 6.4, "Back within 90 days, new-to-Michaels customers")
deps = ["Yarn", "Stitchery", "Jewelry Making", "Unfinished Surfaces", "Balloons", "Party", "Kids"]
rv = (27.5, 19.1, 18.8, 18.8, 18.6, 18.2, 16.8)
cd = CategoryChartData(); cd.categories = list(reversed(deps)); cd.add_series("ret", tuple(reversed(rv)))
ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.55), Inches(1.85), Inches(6.4),
                        Inches(3.2), cd).chart
style_chart(ch); quiet_axes(ch); ch.has_legend = False
ch.value_axis.minimum_scale = 0; ch.value_axis.maximum_scale = 32
pl = ch.plots[0]; pl.gap_width = 45
color_points(pl.series[0], [PURPLE] + [MID] * 6)
for i, v in enumerate(reversed(rv)):
    point_label(pl.series[0], i, f"{v:.1f}%", color=PURPLE if i == 0 else INK,
                pos=XL_LABEL_POSITION.OUTSIDE_END)
text(s, 0.55, 5.1, 6.4, 0.5,
     [[("Lowest of every peer. ", {"bold": True}),
       ("Where in Kids they start (Crafts, Art, Impulse) makes no difference: 47–51% return within a year either way.", {})]],
     size=12)
label(s, 7.5, 1.5, 5.3, "Of those who return, share whose next purchase has Kids")
cd = CategoryChartData(); cd.categories = ["Kids-only first basket", "Kids-led first basket",
                                           "No Kids in first basket"]
cd.add_series("kids next", (53, 56, 21))
ch = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7.5), Inches(1.85), Inches(5.3),
                        Inches(3.2), cd).chart
style_chart(ch); quiet_axes(ch); ch.has_legend = False
ch.value_axis.minimum_scale = 0; ch.value_axis.maximum_scale = 68
ch.category_axis.tick_labels.font.size = Pt(10)
pl = ch.plots[0]; pl.gap_width = 60
color_points(pl.series[0], [PURPLE, PURPLE, MID])
for i, v in enumerate((53, 56, 21)):
    point_label(pl.series[0], i, f"{v}%", pos=XL_LABEL_POSITION.OUTSIDE_END)
text(s, 7.5, 5.1, 5.3, 0.5,
     [[("2.5× the rate of other new customers. ", {"bold": True}),
       ("The problem is the second visit, not loyalty to Kids.", {})]], size=12)
sowhat(s, 5.75, "Welcome series in the first 90 days.",
       "Kids content for new Kids customers, measured on 90-day return. Only 1 in 3 can be emailed today, so capture email at checkout.", h=0.6)
note(s, "Left: first purchase in the department Sep 2025 – Aug 2026, next transaction within 90 days, customers with ≥90 days of history. Right: FY2025 new customers; Kids-led = Kids is the largest department and ≥50% of first-basket spend; next purchase within 12 months. Detail: appendix A13–A15, A24.",
     y=6.45, h=0.4)

# =====================================================================
# Slide 6 - Basket
# =====================================================================
s = new_slide()
title(s, ["", "Kids", " Earns Its Value When Bought With Other Departments"],
      "Baskets of first-time Kids customers, Sep 2025 – Aug 2026, and how the average Kids basket has changed since FY2023.")
label(s, 0.55, 1.5, 6.4, "Kids-only trips vs Kids with other departments")
cd = CategoryChartData(); cd.categories = ["Share of Kids basket dollars", "Share of Kids trips"]
cd.add_series("Kids only", (7.0, 24.2)); cd.add_series("With other departments", (93.0, 75.8))
ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED_100, Inches(0.55), Inches(1.85), Inches(6.4),
                        Inches(2.0), cd).chart
style_chart(ch); quiet_axes(ch)
ch.has_legend = True; ch.legend.position = XL_LEGEND_POSITION.BOTTOM; ch.legend.include_in_layout = False
ch.legend.font.size = Pt(10)
pl = ch.plots[0]; pl.gap_width = 40; pl.overlap = 100
pl.series[0].format.fill.solid(); pl.series[0].format.fill.fore_color.rgb = MID
pl.series[1].format.fill.solid(); pl.series[1].format.fill.fore_color.rgb = PURPLE
for i, v in enumerate((7.0, 24.2)):
    point_label(pl.series[0], i, f"{v:.0f}%", pos=XL_LABEL_POSITION.CENTER)
for i, v in enumerate((93.0, 75.8)):
    point_label(pl.series[1], i, f"{v:.0f}%", color=WHITE, pos=XL_LABEL_POSITION.CENTER)
# stat tiles
tiles = [("$8", "median Kids-only trip"), ("1.9×", "first-basket value when it spans two or more Kids departments"),
         ("30.6%", "of all Kids trips are Kids-only, up from 26.6% in FY2023")]
for i, (big, small) in enumerate(tiles):
    x = 0.55 + i * 2.2
    box(s, x, 4.0, 2.0, 1.25, PALE, MSO_SHAPE.ROUNDED_RECTANGLE, name="Stat tile")
    text(s, x + 0.15, 4.08, 1.7, 0.5, big, size=24, bold=True, color=PURPLE, font=HEAD)
    text(s, x + 0.15, 4.6, 1.75, 0.6, small, size=10, color=INK)
label(s, 7.5, 1.5, 5.3, "Bought most often in the same basket")
cd = CategoryChartData(); comp = ["Craft Art & Adhesives", "Impulse", "Papercrafting",
                                  "Floral Accessories", "Stitchery"]
cvals = (15.5, 13.8, 13.5, 7.4, 7.3)
cd.categories = list(reversed(comp)); cd.add_series("co", tuple(reversed(cvals)))
ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(7.5), Inches(1.85), Inches(5.3),
                        Inches(2.3), cd).chart
style_chart(ch); quiet_axes(ch); ch.has_legend = False
ch.value_axis.minimum_scale = 0; ch.value_axis.maximum_scale = 19
pl = ch.plots[0]; pl.gap_width = 45
color_points(pl.series[0], [MID, MID, NAVY, NAVY, NAVY])
for i, v in enumerate(reversed(cvals)):
    point_label(pl.series[0], i, f"{v:.1f}%", pos=XL_LABEL_POSITION.OUTSIDE_END)
text(s, 7.5, 4.25, 5.3, 1.0,
     [[("On the AUR question: ", {"bold": True}),
       ("since FY2023 a Kids basket holds 10% fewer items at a 13% higher average price, so basket value is up only 1.5%. Kids is becoming a bigger share of a smaller trip.", {})]],
     size=12)
sowhat(s, 5.5, "Place Kids next to the departments it is bought with.",
       "Test adjacencies with Craft Art & Adhesives, Impulse and Papercrafting, and measure the Kids-only share of trips.")
note(s, "Kids-only = no other department in the basket; medians. Same-basket shares count baskets containing each department and overlap. Items and price: average basket containing Kids, FW1–30 FY2023 vs FY2026. Source: cdp_unification_mk.enrich_transactions_behaviour. Detail: appendix A16–A19, A32.",
     y=6.4, h=0.4)

# =====================================================================
# Slide 7 - Top-tier customers
# =====================================================================
s = new_slide()
title(s, ["Most Top Customers Already Buy ", "Kids", "; 3.7M Still Do Not"],
      "Share of each Michaels value tier that bought the department, Sep 2025 – Aug 2026. Core is the highest-value tier.")
label(s, 0.55, 1.5, 7.4, "Bought in the past 12 months, by value tier")
tiers = ["Core", "Aspiring", "Developing", "Uncommitted"]
cd = CategoryChartData(); cd.categories = tiers
cd.add_series("Kids", (65.4, 50.8, 30.8, 18.9))
cd.add_series("Jewelry Making (best peer)", (37.2, 23.8, 12.6, 7.0))
ch = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.55), Inches(1.85), Inches(7.4),
                        Inches(3.4), cd).chart
style_chart(ch); quiet_axes(ch)
ch.has_legend = True; ch.legend.position = XL_LEGEND_POSITION.TOP; ch.legend.include_in_layout = False
ch.legend.font.size = Pt(10)
ch.value_axis.minimum_scale = 0; ch.value_axis.maximum_scale = 75
pl = ch.plots[0]; pl.gap_width = 60; pl.overlap = -10
pl.series[0].format.fill.solid(); pl.series[0].format.fill.fore_color.rgb = PURPLE
pl.series[1].format.fill.solid(); pl.series[1].format.fill.fore_color.rgb = MID
for si, vals_ in ((0, (65.4, 50.8, 30.8, 18.9)), (1, (37.2, 23.8, 12.6, 7.0))):
    for i, v in enumerate(vals_):
        point_label(pl.series[si], i, f"{v:.0f}%", size=10, bold=(si == 0),
                    pos=XL_LABEL_POSITION.OUTSIDE_END)
text(s, 0.55, 5.3, 7.4, 0.4,
     [[("Kids reaches 1.8–2.7× the share of the best peer in every tier.", {"bold": True})]], size=12)
# big stat panel
box(s, 8.4, 1.85, 4.4, 3.45, PALE, MSO_SHAPE.ROUNDED_RECTANGLE, name="Opportunity panel")
text(s, 8.7, 2.0, 3.9, 0.35, "THE OPPORTUNITY", size=10, bold=True, color=GRAY)
text(s, 8.7, 2.35, 3.9, 0.9, "3.7M", size=54, bold=True, color=PURPLE, font=HEAD)
text(s, 8.7, 3.3, 3.9, 0.6, "Core and Aspiring customers who did not buy Kids in the past year (43% of the two tiers)", size=12)
text(s, 8.7, 4.15, 3.9, 1.1,
     [[("Who buys today: ", {"bold": True}),
       ("Core Kids buyers spend $551 a year vs $443 for other Core, and skew 35–44. Teachers spend 2.5× other Kids buyers.", {})]],
     size=11)
sowhat(s, 5.8, "Target Core and Aspiring non-buyers.",
       "Kids offers through paid and owned media to the 3.7M, who already shop Michaels often and can be reached through CRM.", h=0.6)
note(s, "Value tiers are Michaels' own RFM segments; customers without a segment excluded. Reference departments: Party, Unfinished Surfaces, Jewelry Making (highest shown). Sources: cdp_unification_mk.enrich_transactions_behaviour, rfm_segmentation_scores_final. Detail: appendix A10, A20, A27.",
     y=6.5, h=0.35)

# ---------- footers ----------
for i, sl in enumerate(new_slides):
    add_footer(sl, i + 2)

# ---------- reorder: title, new core, appendix divider, v8 slides 2..25, 27..35 ----------
sldIdLst = prs.slides._sldIdLst
ids = list(sldIdLst)
n_orig = len(orig)
orig_ids, new_ids = ids[:n_orig], ids[n_orig:]
order = [orig_ids[0]] + new_ids + [orig_ids[25]] + orig_ids[1:25] + orig_ids[26:]
for el in ids:
    sldIdLst.remove(el)
for el in order:
    sldIdLst.append(el)

# appendix divider text
div = orig[25]
for sh in div.shapes:
    if sh.has_text_frame and sh.text_frame.text.startswith("Detailed"):
        sh.text_frame.paragraphs[0].runs[0].text = "Supporting analysis from v8 · numbered A2–A35 in the original order"
        for r in sh.text_frame.paragraphs[0].runs[1:]:
            r.text = ""

# renumber page numbers on all slides in final order
for k, sl in enumerate(orig, start=1):
    if k > 1:
        set_page(sl, f"A{k}")

prs.save(OUT)
print("saved", OUT, len(prs.slides), "slides")
