from PyQt5.QtGui import QColor

def temperature_to_color(T, Tmin=40, Tmax=140):

    if Tmax <= Tmin:
        Tmax = Tmin + 1e-6

    T = max(Tmin, min(Tmax, T))

    x = (T - Tmin) / (Tmax - Tmin)

    r = int(255 * x)
    g = int(255 * (1 - x))

    return QColor(r, g, 0)