import random

class Mutation:
    def __init__(self, dosen_list, ruangan_list):
        self.dosen_list = dosen_list
        self.ruangan_list = ruangan_list
        self.time_slots = list(range(1, 41)) # Gunakan Slot 1-40
    
    def mutate(self, chromosome, mutation_rate):
        for gene in chromosome.genes:
            if random.random() < mutation_rate:
                mutation_type = random.choice(['time', 'room'])
                
                if mutation_type == 'time':
                    # Mutasi Waktu: Ganti slot ID
                    gene.time_slot = random.choice(self.time_slots)
                
                elif mutation_type == 'room':
                    # Mutasi Ruangan: Ganti ID Ruangan sesuai jenis matkul
                    suitable_rooms = [r for r in self.ruangan_list if r.jenis == gene.jenis]
                    if not suitable_rooms:
                        suitable_rooms = self.ruangan_list
                    gene.ruangan_id = random.choice(suitable_rooms).id
                    
        return chromosome