# management/commands/seed_location_types.py
from django.core.management.base import BaseCommand
from ...models import StockLocationType

class Command(BaseCommand):
    help = 'Populates database with standard stock location types'

    def handle(self, *args, **options):
        LOCATION_TYPES = [
            # General Warehousing
            ('Warehouse', 'Large storage facility'),
            ('Zone', 'Section within a warehouse'),
            ('Aisle', 'Passage between storage racks'),
            ('Rack', 'Storage rack framework'),
            ('Shelf', 'Horizontal storage surface'),
            ('Bin', 'Individual storage container'),
            ('Pallet', 'Unit load platform'),
            
            # Manufacturing Specific
            ('Production Line', 'Assembly line area'),
            ('Workstation', 'Individual production station'),
            ('Tool Crib', 'Secure tool storage'),
            ('QC Area', 'Quality control section'),
            
            # Retail/Frontline
            ('Showroom', 'Customer-facing display area'),
            ('Checkout', 'Point-of-sale storage'),
            ('Backroom', 'Staff-only storage'),
            
            # Specialized Storage
            ('Cold Storage', 'Refrigerated storage area'),
            ('Hazardous Storage', 'Dangerous goods containment'),
            ('Secure Vault', 'High-security storage'),
            ('Outdoor Yard', 'Open-air storage space'),
            
            # Transport Related
            ('Loading Dock', 'Goods transfer area'),
            ('Transit Area', 'Temporary holding zone'),
            ('Cross-Dock', 'Quick transfer staging'),
            
            # Office Storage
            ('Supply Closet', 'Office consumables storage'),
            ('Archive', 'Long-term document storage'),
            ('IT Room', 'Technology equipment storage'),
            
            # Specialized Containers
            ('Drawer', 'Enclosed sliding compartment'),
            ('Cabinet', 'Lockable storage unit'),
            ('Locker', 'Personal storage container'),
            ('Crate', 'Reusable shipping container'),
            ('Tote', 'Reusable plastic container'),
            
            # Vertical Storage
            ('Mezzanine', 'Intermediate floor level'),
            ('Vertical Carousel', 'Automated vertical storage'),
            
            # Bulk Storage
            ('Silo', 'Bulk material container'),
            ('Tank', 'Liquid storage vessel'),
            ('Hopper', 'Bulk material feeder'),
            
            # Special Industry
            ('Pharmacy Shelf', 'Medication storage area'),
            ('Server Rack', 'IT equipment housing'),
            ('Wardrobe', 'Clothing storage system'),
            
            # Retail Specific
            ('Gondola', 'Retail freestanding display'),
            ('Endcap', 'Promotional display area'),
            ('Planogram Area', 'Merchandise layout zone'),
            
            # Temporary Storage
            ('Quarantine Area', 'Isolated holding zone'),
            ('Overflow', 'Temporary excess storage'),
            ('Returns Area', 'Goods return processing'),
            
            # Specialized Racks
            ('Cantilever Rack', 'Long item storage'),
            ('Drive-In Rack', 'High-density pallet storage'),
            ('Mobile Rack', 'Movable storage system'),
            
            # Vertical Transport
            ('Elevator', 'Vertical transport shaft'),
            ('Conveyor', 'Automated transport system'),
            
            # Special Handling
            ('Clean Room', 'Controlled environment'),
            ('Incubator', 'Climate-controlled storage'),
            
            # Documentation
            ('Labeling Station', 'Packaging identification area'),
            ('Kitting Area', 'Assembly preparation zone'),
            
            # Maintenance
            ('Tool Board', 'Visual tool organization'),
            ('Maintenance Cage', 'Secure equipment storage'),
            
            # E-commerce
            ('Picking Station', 'Order fulfillment area'),
            ('Packing Bench', 'Order preparation surface'),
            ('Returns Processing', 'Customer return handling')
        ]

        created_count = 0
        for name, desc in LOCATION_TYPES:
            _, created = StockLocationType.objects.get_or_create(
                name=name,
                defaults={'description': desc}
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Created {name}'))
            else:
                self.stdout.write(self.style.WARNING(f'{name} already exists'))

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully seeded {created_count}/{len(LOCATION_TYPES)} location types'
            )
        )