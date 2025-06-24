# your_app/management/commands/seed_units.py
from django.core.management.base import BaseCommand
from ...models import Unit

class Command(BaseCommand):
    help = 'Populates the database with common inventory units'

    def handle(self, *args, **kwargs):
        unit_data = [
            {
                'dimension_type': Unit.DimensionType.MASS,
                'units': [
                    {'name': 'Kilogram', 'abbreviated_name': 'kg'},
                    {'name': 'Gram', 'abbreviated_name': 'g', 'conversion_factor': 0.001},
                    {'name': 'Milligram', 'abbreviated_name': 'mg', 'conversion_factor': 0.000001},
                    {'name': 'Metric Ton', 'abbreviated_name': 't', 'conversion_factor': 1000},
                    {'name': 'Pound', 'abbreviated_name': 'lb', 'conversion_factor': 0.453592},
                    {'name': 'Ounce', 'abbreviated_name': 'oz', 'conversion_factor': 0.0283495},
                    {'name': 'Stone', 'abbreviated_name': 'st', 'conversion_factor': 6.35029},
                    {'name': 'Carat', 'abbreviated_name': 'ct', 'conversion_factor': 0.0002},
                ]
            },
            {
                'dimension_type': Unit.DimensionType.VOLUME,
                'units': [
                    {'name': 'Liter', 'abbreviated_name': 'L'},
                    {'name': 'Milliliter', 'abbreviated_name': 'mL', 'conversion_factor': 0.001},
                    {'name': 'Cubic Meter', 'abbreviated_name': 'm³', 'conversion_factor': 1000},
                    {'name': 'Gallon', 'abbreviated_name': 'gal', 'conversion_factor': 3.78541},
                    {'name': 'Quart', 'abbreviated_name': 'qt', 'conversion_factor': 0.946353},
                    {'name': 'Pint', 'abbreviated_name': 'pt', 'conversion_factor': 0.473176},
                    {'name': 'Fluid Ounce', 'abbreviated_name': 'fl oz', 'conversion_factor': 0.0295735},
                    {'name': 'Barrel', 'abbreviated_name': 'bbl', 'conversion_factor': 158.987},
                    {'name': 'Cubic Foot', 'abbreviated_name': 'ft³', 'conversion_factor': 28.3168},
                    {'name': 'Cubic Inch', 'abbreviated_name': 'in³', 'conversion_factor': 0.0163871},
                ]
            },
            {
                'dimension_type': Unit.DimensionType.LENGTH,
                'units': [
                    {'name': 'Meter', 'abbreviated_name': 'm'},
                    {'name': 'Centimeter', 'abbreviated_name': 'cm', 'conversion_factor': 0.01},
                    {'name': 'Millimeter', 'abbreviated_name': 'mm', 'conversion_factor': 0.001},
                    {'name': 'Kilometer', 'abbreviated_name': 'km', 'conversion_factor': 1000},
                    {'name': 'Inch', 'abbreviated_name': 'in', 'conversion_factor': 0.0254},
                    {'name': 'Foot', 'abbreviated_name': 'ft', 'conversion_factor': 0.3048},
                    {'name': 'Yard', 'abbreviated_name': 'yd', 'conversion_factor': 0.9144},
                    {'name': 'Mile', 'abbreviated_name': 'mi', 'conversion_factor': 1609.34},
                    {'name': 'Nautical Mile', 'abbreviated_name': 'nmi', 'conversion_factor': 1852},
                    {'name': 'Light Year', 'abbreviated_name': 'ly', 'conversion_factor': 9.4607e15},
                ]
            },
            {
                'dimension_type': Unit.DimensionType.PIECE,
                'units': [
                    {'name': 'Each', 'abbreviated_name': 'ea'},
                    {'name': 'Dozen', 'abbreviated_name': 'dz', 'conversion_factor': 12},
                    {'name': 'Hundred', 'abbreviated_name': 'C', 'conversion_factor': 100},
                    {'name': 'Thousand', 'abbreviated_name': 'M', 'conversion_factor': 1000},
                    {'name': 'Gross', 'abbreviated_name': 'gr', 'conversion_factor': 144},
                    {'name': 'Great Gross', 'abbreviated_name': 'ggr', 'conversion_factor': 1728},
                    {'name': 'Ream', 'abbreviated_name': 'rm', 'conversion_factor': 500},
                    {'name': 'Carton', 'abbreviated_name': 'ctn'},
                    {'name': 'Case', 'abbreviated_name': 'cse'},
                    {'name': 'Pack', 'abbreviated_name': 'pk'},
                ]
            },
            {
                'dimension_type': Unit.DimensionType.TIME,
                'units': [
                    {'name': 'Second', 'abbreviated_name': 's'},
                    {'name': 'Minute', 'abbreviated_name': 'min', 'conversion_factor': 60},
                    {'name': 'Hour', 'abbreviated_name': 'hr', 'conversion_factor': 3600},
                    {'name': 'Day', 'abbreviated_name': 'd', 'conversion_factor': 86400},
                    {'name': 'Week', 'abbreviated_name': 'wk', 'conversion_factor': 604800},
                    {'name': 'Month', 'abbreviated_name': 'mo', 'conversion_factor': 2.6298e6},
                    {'name': 'Year', 'abbreviated_name': 'yr', 'conversion_factor': 3.15576e7},
                    {'name': 'Shift', 'abbreviated_name': 'shft'},
                    {'name': 'Business Day', 'abbreviated_name': 'b day'},
                    {'name': 'Work Hour', 'abbreviated_name': 'wh'},
                ]
            },
            {
                'dimension_type': Unit.DimensionType.CUSTOM,
                'units': [
                    {'name': 'Pallet', 'abbreviated_name': 'plt'},
                    {'name': 'Crate', 'abbreviated_name': 'crt'},
                    {'name': 'Bundle', 'abbreviated_name': 'bdl'},
                    {'name': 'Roll', 'abbreviated_name': 'rl'},
                    {'name': 'Bag', 'abbreviated_name': 'bg'},
                    {'name': 'Drum', 'abbreviated_name': 'drm'},
                    {'name': 'Tote', 'abbreviated_name': 'tte'},
                    {'name': 'Tank', 'abbreviated_name': 'tnk'},
                    {'name': 'Container', 'abbreviated_name': 'cnt'},
                    {'name': 'Skid', 'abbreviated_name': 'skd'},
                    {'name': 'Bin', 'abbreviated_name': 'bn'},
                    {'name': 'Box', 'abbreviated_name': 'bx'},
                    {'name': 'Canister', 'abbreviated_name': 'can'},
                    {'name': 'Jar', 'abbreviated_name': 'jr'},
                    {'name': 'Tube', 'abbreviated_name': 'tb'},
                    {'name': 'Sack', 'abbreviated_name': 'sk'},
                ]
            }
        ]

        for group in unit_data:
            dimension_type = group['dimension_type']
            units = group['units']
            
            # Create base unit first
            base_unit_data = units[0]
            base_unit, created = Unit.objects.get_or_create(
                dimension_type=dimension_type,
                name=base_unit_data['name'],
                defaults={
                    'abbreviated_name': base_unit_data['abbreviated_name'],
                    'conversion_factor': 1.0
                }
            )
            
            # Create other units in the group
            for unit in units[1:]:
                Unit.objects.get_or_create(
                    dimension_type=dimension_type,
                    name=unit['name'],
                    defaults={
                        'abbreviated_name': unit['abbreviated_name'],
                        'base_unit': base_unit,
                        'conversion_factor': unit.get('conversion_factor', 1.0)
                    }
                )

        self.stdout.write(self.style.SUCCESS('Successfully populated units'))